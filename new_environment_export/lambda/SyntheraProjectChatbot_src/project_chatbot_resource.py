"""
Lambda handler for project-level chatbot.
Resolver for sendProjectChatMessage AppSync mutation.

Flow:
    1. Receive CallIds (list) + Message from AppSync
    2. Retrieve relevant chunks from Bedrock KB filtered by CallIds
    3. Generate answer via Nova Lite
    4. Push answer back to AppSync via addChatToken mutation
"""

import json
import logging
import os
import re
import uuid

import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION          = os.environ.get("BEDROCK_REGION", "us-east-1")
KB_ID           = os.environ.get("KB_ID")
MODEL_ID        = os.environ.get("KB_MODEL_ID")
APPSYNC_URL     = os.environ.get("APPSYNC_GRAPHQL_URL")

kb_client       = boto3.client("bedrock-agent-runtime", region_name=REGION)
model_client    = boto3.client("bedrock-runtime", region_name=REGION)
session         = boto3.Session()
http            = urllib3.PoolManager()

ADD_CHAT_TOKEN_MUTATION = """
mutation AddChatToken($input: AddChatTokenInput!) {
  addChatToken(input: $input) {
    CallId
    MessageId
    Token
    IsComplete
    Sequence
  }
}
"""


def _normalize(text: str) -> str:
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'[^\x00-\x7FÀ-ɏḀ-ỿ]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _call_appsync(payload: dict):
    """Call AppSync mutation using IAM SigV4 auth."""
    body    = json.dumps(payload).encode("utf-8")
    request = AWSRequest(method="POST", url=APPSYNC_URL, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(session.get_credentials(), "appsync", REGION).add_auth(request)

    response = http.request(
        "POST",
        APPSYNC_URL,
        body=body,
        headers=dict(request.headers)
    )
    return json.loads(response.data.decode("utf-8"))


def _retrieve(user_query: str, call_ids: list) -> list:
    """Retrieve relevant chunks from KB filtered by CallIds."""
    kb_query = f"{user_query} from the meeting transcripts"
    response = kb_client.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": kb_query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 25,
                "filter": {
                    "in": {
                        "key":   "CallId",
                        "value": call_ids
                    }
                }
            }
        }
    )
    return response.get("retrievalResults", [])


def _generate(user_query: str, chunks: list) -> str:
    """Generate answer via Nova Lite using retrieved context."""
    context = "\n\n".join([
        f"[Source {i+1}]: {c.get('content', {}).get('text', '')}"
        for i, c in enumerate(chunks)
    ])

    prompt = f"""You are a meeting intelligence agent. Your only source of knowledge is the meeting transcript context provided below. You have no other knowledge and must not use anything outside of it.

Your job is to perform any operation the user requests — such as summarizing, extracting action items, listing decisions, identifying who said what, finding follow-ups, or answering specific questions — using only what is in the meeting context.

Rules:
- Operate strictly on the meeting context below. Never use outside knowledge.
- Do not hallucinate, assume, or invent anything not explicitly present in the context. Never add fields like Owner, Due Date, Description, or Priority unless explicitly mentioned in the transcript.
- Perform exactly the operation the user asks. Nothing more, nothing less.
- If the requested information is not found in the context, respond only with: "This information was not found in the available meeting transcripts."
- For lists (action items, decisions, follow-ups), always use numbered format: 1. 2. 3. with one item per line and no extra fields or descriptions. Remove duplicate items — each item should appear only once.
- For questions and summaries, write in plain sentences.
- Do not use markdown formatting, asterisks, bold, italics, or emojis.
- Keep the response concise and relevant to what the user asked.

Meeting Context:
{context}

User Request: {user_query}

Response:"""

    response = model_client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}]
    )
    return _normalize(response["output"]["message"]["content"][0]["text"])


def _push_token(call_id: str, message_id: str, token: str, sequence: int, is_complete: bool):
    """Push a token back to AppSync via addChatToken mutation."""
    payload = {
        "query": ADD_CHAT_TOKEN_MUTATION,
        "variables": {
            "input": {
                "CallId":     call_id,
                "MessageId":  message_id,
                "Token":      token,
                "IsComplete": is_complete,
                "Sequence":   sequence
            }
        }
    }
    result = _call_appsync(payload)
    if "errors" in result:
        logger.error(f"AppSync addChatToken error: {result['errors']}")


def lambda_handler(event, context):
    try:
        logger.info(f"Project Chatbot - event: {json.dumps(event)}")

        arguments  = event.get("arguments", {})
        input_data = arguments.get("input", {})

        call_ids   = input_data.get("CallIds", [])
        message    = input_data.get("Message", "")

        if not call_ids or not message:
            raise ValueError("CallIds and Message are required")

        message_id = input_data.get("MessageId") or f"chat-{uuid.uuid4()}"
        call_id    = call_ids[0]  # use first CallId for subscription filtering

        logger.info(f"Querying KB with {len(call_ids)} call ID(s): {call_ids}")

        chunks = _retrieve(message, call_ids)
        logger.info(f"Retrieved {len(chunks)} chunk(s) from KB")

        if not chunks:
            answer  = "No relevant information was found in the available meetings for your question."
            sources = []
        else:
            answer  = _generate(message, chunks)
            sources = list({c.get("metadata", {}).get("CallId", "") for c in chunks if c.get("metadata", {}).get("CallId")})
        logger.info(f"Answer generated — length: {len(answer)} chars")

        _push_token(call_id, message_id, answer, sequence=1, is_complete=True)
        logger.info("Answer pushed to AppSync via addChatToken")

        return {
            "MessageId": message_id,
            "Status":    "PROCESSING",
            "CallId":    call_id,
            "Response":  answer,
            "Sources":   sources
        }

    except Exception as e:
        logger.error(f"Error in project chatbot: {str(e)}")
        raise e
