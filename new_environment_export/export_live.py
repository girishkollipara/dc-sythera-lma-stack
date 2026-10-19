"""Read-only export of the live LMASA stack customizations (same layout as new_environment_export/).

Usage: python export_live.py <out_dir>
Only Get/List/Describe calls. Secret-looking values are redacted.
"""
import io
import json
import os
import re
import shutil
import sys
import urllib.request
import zipfile

import boto3

REGION = "us-east-1"
STACK = "LMASA"
API_ID = "lo3gpjswjjdqrh6xn5xkgipeme"
ECR_REPO = "lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx"
TASKDEF_FAMILY = "LMASA-VIRTUALPARTICIPANTSTACK-1IAVIZT1439R2VirtualParticipantTaskDef"
SFN_ARN = "arn:aws:states:us-east-1:528757797189:stateMachine:LMASA-LMAVirtualParticipantScheduler"
# SyntheraProjectChatbot: Synthera Lambda behind Mutation.sendProjectChatMessage (AppSync datasource).
LAMBDAS = ["LMASA-VPScheduler", "LMASA-MeetingEnrichment", "LMASA-MeetingControlsLambda", "SyntheraProjectChatbot"]
TABLES = {
    "EventSourcingTable": "LMASA-AISTACK-4VKHCQQY3CJR-EventSourcingTable-V49M8Y0JQODE",
    "ProjectMembership": "LMASA-ProjectMembership",
    "VirtualParticipantShareIndex": "LMASA-VirtualParticipantShareIndex",
    "VirtualParticipant": "LMASA-AISTACK-4VKHCQQY3CJR-VirtualParticipant",
}
USER_POOL_ID = "us-east-1_pOCRCo1Wx"
ECS_CLUSTER = "LMASA-VIRTUALPARTICIPANTSTACK-1IAVIZT1439R2-Cluster-hEH0LkElgiwp"
ECS_SERVICE = "LMASA-vp-service-FARGATE"
# Roles outside LMA that were granted access to LMA resources (Synthera backend).
EXTERNAL_ROLES = {
    "dc-synthera-rest-api-and-webhook-event-processor-dev1-role": ["appsync_virtual_participant_access"],
    "dc-synthera-ws-lambda-exec-role-dev": ["synthera-ws-lambda-inline"],
}
VENDORED = re.compile(r"^(pymongo|bson|gridfs|dns|dnspython|cryptography|cffi|_cffi_backend|pycparser|jwt|PyJWT)"
                      r"|\.dist-info/|\.libs/|__pycache__/")
SECRET_KEY = re.compile(r"PASS|SECRET|TOKEN|PRIVATE|API_?KEY|CREDENTIAL|CONNECTION_STRING", re.I)
REDACTED = "<REDACTED - read from the live Lambda/Secrets Manager, never commit>"

OUT = sys.argv[1]
s = boto3.Session(region_name=REGION)
lam, appsync, ddb, iam, ecs, ecr, sfn, cfn, idp = (s.client(x) for x in (
    "lambda", "appsync", "dynamodb", "iam", "ecs", "ecr", "stepfunctions", "cloudformation", "cognito-idp"))


def strip_meta(d):
    if isinstance(d, dict):
        d.pop("ResponseMetadata", None)
    return d


def write(rel, data):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(strip_meta(data), f, indent=1, default=iso)
            f.write("\n")
    print("  wrote", rel)


def is_secret(key, value):
    return bool(value) and SECRET_KEY.search(key) and not re.search(r"(Url|URL|Endpoint|ENDPOINT|Arn|ARN|Id)$", key)


def redact_env(cfg):
    env = (cfg.get("Environment") or {}).get("Variables") or {}
    for k in env:
        if is_secret(k, env[k]):
            env[k] = REDACTED
    return cfg


def iso(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)


# ---- CloudFormation root -------------------------------------------------
print("cfn")
stack = cfn.describe_stacks(StackName=STACK)
for o in stack["Stacks"][0].get("Outputs", []):
    if is_secret(o["OutputKey"], o["OutputValue"]):
        o["OutputValue"] = REDACTED
for p in stack["Stacks"][0].get("Parameters", []):
    if is_secret(p["ParameterKey"], p.get("ParameterValue")):
        p["ParameterValue"] = REDACTED
write("cfn_root_stack.json", stack)
write("cfn_root_outputs.json", {o["OutputKey"]: o["OutputValue"] for o in stack["Stacks"][0].get("Outputs", [])})

# ---- Lambdas ---------------------------------------------------------------
print("lambda")
roles = set()
for name in LAMBDAS:
    fn = lam.get_function(FunctionName=name)
    cfg = redact_env(fn["Configuration"])
    roles.add(cfg["Role"].split("/")[-1])
    write(f"lambda/{name}.config.json", cfg)
    blob = urllib.request.urlopen(fn["Code"]["Location"]).read()
    with open(os.path.join(OUT, "lambda", f"{name}.zip"), "wb") as f:
        f.write(blob)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        # Only our own files; vendored libraries stay inside the .zip (versions in requirements.txt).
        own = [n for n in z.namelist()
               if not n.endswith("/") and not VENDORED.search(n) and not n.startswith("__MACOSX/")]
        for n in own:
            z.extract(n, os.path.join(OUT, "lambda", f"{name}_src"))
    print(f"  {name}: {len(blob)} bytes, hand-written files: {own}")

esm = lam.list_event_source_mappings(FunctionName="LMASA-MeetingEnrichment")
write("lambda/enrichment_event_source_mappings.json", esm)

# The hand-written enrichment files, separated from the vendored libraries.
src = os.path.join(OUT, "lambda", "LMASA-MeetingEnrichment_src")
for n in os.listdir(src):
    if n.endswith(".py") and os.path.isfile(os.path.join(src, n)):
        os.makedirs(os.path.join(OUT, "lambda", "enrichment_lambda_source"), exist_ok=True)
        shutil.copyfile(os.path.join(src, n), os.path.join(OUT, "lambda", "enrichment_lambda_source", n))

# ---- AppSync ---------------------------------------------------------------
print("appsync")
write("appsync/graphql-api.json", appsync.get_graphql_api(apiId=API_ID))
schema = appsync.get_introspection_schema(apiId=API_ID, format="SDL", includeDirectives=True)["schema"]
write("appsync/schema.graphql", schema.read().decode("utf-8"))

def paged(call, key, **kw):
    items, token = [], None
    while True:
        r = call(**kw, **({"nextToken": token} if token else {}))
        items += r.get(key, [])
        token = r.get("nextToken")
        if not token:
            return items

dss = paged(appsync.list_data_sources, "dataSources", apiId=API_ID)
write("appsync/data-sources.json", dss)
for ds in dss:
    if ds.get("serviceRoleArn"):
        roles.add(ds["serviceRoleArn"].split("/")[-1])

funcs = paged(appsync.list_functions, "functions", apiId=API_ID)
fid_to_name = {f["functionId"]: f["name"] for f in funcs}
write("appsync/_functions_index.json", [
    {"name": f["name"], "functionId": f["functionId"], "dataSourceName": f.get("dataSourceName"),
     "runtime": (f.get("runtime") or {}).get("name", "VTL")} for f in funcs])
for f in funcs:
    write(f"appsync/functions/{f['name']}.json", f)

types = paged(appsync.list_types, "types", apiId=API_ID, format="SDL")
index = []
for t in sorted(x["name"] for x in types):
    for r in paged(appsync.list_resolvers, "resolvers", apiId=API_ID, typeName=t):
        write(f"appsync/resolvers/{r['typeName']}.{r['fieldName']}.json", r)
        index.append({
            "field": f"{r['typeName']}.{r['fieldName']}", "kind": r.get("kind"),
            "dataSourceName": r.get("dataSourceName"),
            "pipeline": [fid_to_name.get(i, i) for i in (r.get("pipelineConfig") or {}).get("functions", [])],
            "runtime": (r.get("runtime") or {}).get("name", "VTL")})
write("appsync/_resolvers_index.json", sorted(index, key=lambda x: x["field"]))

# ---- DynamoDB --------------------------------------------------------------
print("dynamodb")
for label, table in TABLES.items():
    write(f"dynamodb/{label}.json", ddb.describe_table(TableName=table))

# ---- ECS / ECR / Step Functions -------------------------------------------
print("ecs/ecr/sfn")
sched_env = lam.get_function_configuration(FunctionName="LMASA-VPScheduler")["Environment"]["Variables"]
current_td = sched_env["TASK_DEFINITION_ARN"]
current_rev = current_td.rsplit(":", 1)[1]
for rev in ("3", current_rev):
    td = ecs.describe_task_definition(taskDefinition=f"{TASKDEF_FAMILY}:{rev}", include=["TAGS"])
    for c in td["taskDefinition"]["containerDefinitions"]:
        for e in c.get("environment", []):
            if is_secret(e["name"], e.get("value")):
                e["value"] = REDACTED
    write(f"ecs/taskdef_rev{rev}.json", td)
svc = ecs.describe_services(cluster=ECS_CLUSTER, services=[ECS_SERVICE])
write("ecs/service.json", svc)
write("ecr/describe-images.json", {"imageDetails": paged(ecr.describe_images, "imageDetails", repositoryName=ECR_REPO)})

sm = sfn.describe_state_machine(stateMachineArn=SFN_ARN)
write("stepfunction/LMAVirtualParticipantScheduler.json", sm)
write("stepfunction/definition_only.json", json.loads(sm["definition"]))
roles.add(sm["roleArn"].split("/")[-1])
roles.add(sched_env["SCHEDULER_ROLE_ARN"].split("/")[-1])

# ---- IAM: roles touched by the customizations ------------------------------
print("iam")
CFN_ROLES = set()
def walk(name):
    for page in cfn.get_paginator("list_stack_resources").paginate(StackName=name):
        for r in page["StackResourceSummaries"]:
            if r["ResourceType"] == "AWS::IAM::Role":
                CFN_ROLES.add(r.get("PhysicalResourceId"))
            elif r["ResourceType"] == "AWS::CloudFormation::Stack" and r.get("PhysicalResourceId"):
                walk(r["PhysicalResourceId"])
walk(STACK)
# Stack-owned roles whose policies were edited by hand (from the change inventory).
MODIFIED_STACK_ROLES = {"LMASA-VIRTUALPARTICIPANTS-VirtualParticipantSchedul-7TrZcy7KmFtq",
                        "LMASA-VIRTUALPARTICIPANTST-VPSchedulerExecutionRole-X4Ig7X8qtTtT",
                        # inline policies edited 2026-08-10 for the VP share index / TypeDateIndex
                        "LMASA-AISTACK-4VKHCQQY3CJ-VirtualParticipantDataSou-IJlzjslOWLIo",
                        "LMASA-AISTACK-4VKHCQQY3CJR-AppSyncDynamoDbRole-K65SfE5yoWP2"}
for role in sorted(roles):
    try:
        r = iam.get_role(RoleName=role)
    except iam.exceptions.NoSuchEntityException:
        continue
    stock = role in CFN_ROLES
    if stock and role not in MODIFIED_STACK_ROLES:
        continue
    inline = iam.list_role_policies(RoleName=role)["PolicyNames"]
    attached = iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]
    write(f"iam/{role}.role.json", {"Role": r["Role"], "AttachedPolicies": attached, "CloudFormationOwned": stock})
    for p in inline:
        write(f"iam/{role}__{p}.json", iam.get_role_policy(RoleName=role, PolicyName=p))

for role, policies in EXTERNAL_ROLES.items():
    for p in policies:
        try:
            write(f"iam/external/{role}__{p}.json", iam.get_role_policy(RoleName=role, PolicyName=p))
        except iam.exceptions.NoSuchEntityException:
            print("  missing", role, p)

# ---- Cognito groups ---------------------------------------------------------
print("cognito")
groups = paged_cognito = []
token = None
while True:
    r = idp.list_groups(UserPoolId=USER_POOL_ID, **({"NextToken": token} if token else {}))
    groups += r["Groups"]
    token = r.get("NextToken")
    if not token:
        break
write("cognito/user-pool.json", idp.describe_user_pool(UserPoolId=USER_POOL_ID))
# Microsoft Entra ID SAML provider - added by hand 2026-05-15, not in any stack.
write("cognito/identity-provider-MicrosoftEntraID.json",
      idp.describe_identity_provider(UserPoolId=USER_POOL_ID, ProviderName="MicrosoftEntraID"))
for c in idp.list_user_pool_clients(UserPoolId=USER_POOL_ID, MaxResults=60)["UserPoolClients"]:
    client = idp.describe_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=c["ClientId"])
    client["UserPoolClient"].pop("ClientSecret", None)
    write(f"cognito/client-{c['ClientName']}.json", client)
write("cognito/groups.json", [{k: g.get(k) for k in ("GroupName", "Description", "Precedence", "RoleArn",
                                                     "CreationDate", "LastModifiedDate")} for g in groups])

print("done; task def in use:", current_td)
