# LMA SSM Parameter Reference (for external consumers, e.g. Synthera's Terraform)

After every successful deploy, the Jenkins pipeline's `Export Outputs to SSM`
stage runs [`scripts/export_stack_resources_to_ssm.sh`](../scripts/export_stack_resources_to_ssm.sh)
and publishes values to AWS Systems Manager Parameter Store, region
`us-east-1`, account `528757797189`. This is the supported way for other
systems (Terraform, other pipelines, scripts) to read LMA stack details
without needing direct CloudFormation access.

## Where the logic lives

File: [`scripts/export_stack_resources_to_ssm.sh`](../scripts/export_stack_resources_to_ssm.sh)

| What | Lines |
|---|---|
| Reads the stack's CloudFormation `Outputs` and writes one SSM parameter per output | 49-68 |
| Writes the full resource inventory (all 300+ resources across every nested stack) to S3, and writes **one** pointer parameter to it | 73-128 |

**Only CloudFormation `Outputs` become individual SSM parameters.** Nothing
else does. What counts as an "Output" is decided in the stack templates
themselves (`Outputs:` sections in `lma-main.yaml` and the nested stack
templates) - the export script doesn't curate or filter anything; it just
mirrors whatever the templates already chose to expose. If a value Synthera
needs isn't here, it needs to be added as a CloudFormation Output in the
relevant template first.

## Naming pattern

```
/lma/<environment>/outputs/<OutputKey>
```

`<environment>` is `qa`, `dev01`, or `prod`. `<OutputKey>` matches the
CloudFormation Output name exactly (case-sensitive).

There is also one non-output pointer parameter:

```
/lma/<environment>/full-resource-inventory
```

whose value is an `s3://` URI to a JSON file listing every physical resource
in the stack and its nested stacks (Lambda ARNs, DynamoDB tables, IAM roles,
etc.) - written for human/audit use, not meant to be consumed
resource-by-resource. If Synthera ever needs something from here regularly,
the fix is to add it as a proper CloudFormation Output instead, not to parse
this file.

## Current parameters (qa environment, live as of last deploy)

| SSM Parameter Name | Description |
|---|---|
| `/lma/qa/outputs/ApplicationCloudfrontEndpoint` | LMA User Interface URL |
| `/lma/qa/outputs/CallDataStreamArn` | ARN of the Kinesis Data Stream that call data is written to |
| `/lma/qa/outputs/CallDataStreamName` | Name of that same Kinesis Data Stream |
| `/lma/qa/outputs/ChromeExtensionDownloadUrl` | LMA Chrome browser extension download link |
| `/lma/qa/outputs/CognitoUserPoolClientId` | Cognito User Pool Client ID |
| `/lma/qa/outputs/CognitoUserPoolTokenIssuerUrl` | Cognito User Pool token issuer URL |
| `/lma/qa/outputs/CustomChatButtonConfig` | Custom Strands agent chat button config (overrides, preserved across updates) |
| `/lma/qa/outputs/CustomNovaSonicConfig` | Custom Nova Sonic voice assistant config (overrides, preserved across updates) |
| `/lma/qa/outputs/DefaultChatButtonConfig` | Default Strands agent chat button config (read-only reference) |
| `/lma/qa/outputs/DefaultNovaSonicConfig` | Default Nova Sonic voice assistant config (read-only reference) |
| `/lma/qa/outputs/FetchTranscriptLambdaArn` | ARN of the Lambda that exports a call transcript as a string |
| `/lma/qa/outputs/LLMCustomPromptSummaryTemplate` | Custom summary prompt overrides (preserved across updates) |
| `/lma/qa/outputs/LLMDefaultPromptSummaryTemplate` | Default summary prompts (read-only reference) |
| `/lma/qa/outputs/LMASettingsParameterName` | Name of the (separate) Parameter Store entry holding general LMA settings |
| `/lma/qa/outputs/LMAWebsocketEndpoint` | Websocket endpoint for audio streaming integration |
| `/lma/qa/outputs/LocalUITestingEnv` | Contents to copy into a local `.env` file for UI dev testing |
| `/lma/qa/outputs/MCPServerApiKeyEndpoint` | REST API Gateway endpoint for API-key-authenticated MCP access |
| `/lma/qa/outputs/MCPServerAuthorizationURL` | OAuth authorization endpoint (browser login redirect) for the MCP server |
| `/lma/qa/outputs/MCPServerClientId` | Cognito Client ID for external apps authenticating to the MCP server |
| `/lma/qa/outputs/MCPServerClientSecret` | Cognito Client Secret for the same - **sensitive, handle like a credential** |
| `/lma/qa/outputs/MCPServerEndpoint` | MCP server endpoint for external apps (Claude Desktop, Quick Suite, custom clients) to read LMA meeting data |
| `/lma/qa/outputs/MCPServerTokenURL` | OAuth token endpoint to exchange an authorization code for an access token |
| `/lma/qa/outputs/MCPServerUserPoolId` | Cognito User Pool ID used for MCP server authentication |
| `/lma/qa/outputs/OAuthCallbackUrl` | OAuth callback URL to register with an external OAuth provider |
| `/lma/qa/outputs/QuickSightManifestUrl` | S3 URL of the QuickSight manifest for the recordings bucket |
| `/lma/qa/outputs/RecordingsS3Bucket` | S3 bucket containing all call recordings |
| `/lma/qa/outputs/RecordingsS3BucketArn` | ARN of that same bucket |
| `/lma/qa/outputs/SNSCategoryTopicName` | SNS topic name for matched-category / alert notifications |
| `/lma/qa/outputs/EventSourcingTableName` | Name of the DynamoDB table storing transcriptions and events |
| `/lma/qa/outputs/EventSourcingTableStreamArn` | ARN of that table's DynamoDB Stream |
| `/lma/qa/outputs/VirtualParticipantTableName` | Name of the DynamoDB table storing Virtual Participant records |
| `/lma/qa/outputs/VirtualParticipantTableStreamArn` | ARN of that table's DynamoDB Stream |
| `/lma/qa/outputs/DynamoDbKmsKeyArn` | ARN of the customer-managed KMS key used to encrypt LMA's DynamoDB tables |
| `/lma/qa/outputs/CognitoUserPoolId` | Cognito User Pool ID (the pool itself, not the app client) |
| `/lma/qa/outputs/CognitoUserPoolArn` | Cognito User Pool ARN |
| `/lma/qa/outputs/TranscriptKnowledgeBaseId` | ID of the Bedrock Knowledge Base that indexes meeting transcripts - only present if this environment was deployed with `TranscriptKnowledgeBase` set to create one |
| `/lma/qa/outputs/TranscriptKnowledgeBaseArn` | ARN of that same Knowledge Base |
| `/lma/qa/full-resource-inventory` | `s3://` pointer to the full per-deploy resource dump (see above - audit use, not for routine lookups) |

**Note on the Knowledge Base outputs:** this template has two separate Bedrock
Knowledge Base concepts. `TranscriptKnowledgeBaseId`/`Arn` above cover the one
that indexes meeting transcripts (nested stack `TRANSCRIPTBEDROCKKB`, created
when the `TranscriptKnowledgeBase` parameter is set to create one - this is
the one active in `qa` today). A second, separate KB exists for the
meeting-assistant's general-purpose document search (`BedrockKnowledgeBaseId`
parameter / nested stack `BEDROCKKB`) - it is not currently created in `qa`
(that parameter is blank), so there is nothing to export for it yet. If a
future environment enables that feature, it will need its own Output added
the same way.

For `dev01` and `prod`, the same list applies with `/lma/dev01/...` and
`/lma/prod/...` prefixes, once those environments are deployed through the
same pipeline.

## How Synthera should reference these

Read-only access via Terraform's built-in SSM data source - no custom
integration code needed:

```hcl
data "aws_ssm_parameter" "lma_ui_url" {
  name = "/lma/qa/outputs/ApplicationCloudfrontEndpoint"
}

data "aws_ssm_parameter" "lma_mcp_endpoint" {
  name = "/lma/qa/outputs/MCPServerEndpoint"
}

output "example" {
  value = data.aws_ssm_parameter.lma_ui_url.value
}
```

For the two sensitive values (`MCPServerClientSecret` and anything else
tagged sensitive above), treat the resulting Terraform value as a secret -
don't log it or write it into plain state-file outputs that get printed in
CI logs.

### Required IAM permissions

Whatever AWS identity Synthera's Terraform runs as needs read access to
these specific parameters, e.g.:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadLmaQaOutputs",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:us-east-1:528757797189:parameter/lma/qa/outputs/*"
    }
  ]
}
```

This needs to be granted explicitly (either as a policy on Synthera's own
IAM role/user, or as a cross-account trust if Synthera runs from a different
AWS account) - it does not exist yet and is a separate action item from
anything in this repo.

## Freshness

These parameters are only as current as the last successful pipeline run
for that environment. There's no drift detection - if someone changes the
stack outside the pipeline (e.g. manual `aws cloudformation deploy`), the
SSM values won't update until the pipeline runs again.
