import os
import json
import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

scheduler_client = boto3.client('scheduler')
ecs_client = boto3.client('ecs')
cognito_client = boto3.client('cognito-idp')

USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')

# Mirrors INTRO_MESSAGE on the task definition. Kept here so the {LMA_USER}
# placeholder can be expanded with the person's NAME before the container sees
# it. If the task definition's INTRO_MESSAGE is ever edited, update this too.
INTRO_MESSAGE_TEMPLATE = os.environ.get('INTRO_MESSAGE_TEMPLATE', '')

def resolve_vp_identity(new_image):
    """Resolve (LMA_USER, LMA_IDENTITY, INTRO_MESSAGE) for the Virtual Participant.

    Three separate concerns, deliberately kept in three separate variables:

      LMA_USER      the Cognito username. The container copies this into the
                    START event's AgentId, and CallEventProcessor falls back to
                    AgentId for the meeting's Owner whenever the access token is
                    absent - which is ALWAYS for scheduled meetings, since there
                    is no user session when EventBridge fires. So this value
                    decides who OWNS the meeting and must stay the username:
                    the AppSync resolvers compare Owner to ctx.identity.username.
      LMA_IDENTITY  "<First>'s AI Assistant" - the name shown in the participant
                    list. Display only.
      INTRO_MESSAGE the spoken intro, with the person's full name substituted in
                    here rather than in the container. That keeps the email/name
                    out of LMA_USER entirely.

    Putting a display value in LMA_USER silently breaks ownership - it did on
    2026-07-16, costing 8 meetings their owner.
    """
    owner = new_image.get('owner', {}).get('S', '')
    email = new_image.get('ownerEmail', {}).get('S') or ''
    given_name = ''
    full_name = ''

    if owner and USER_POOL_ID:
        try:
            response = cognito_client.admin_get_user(UserPoolId=USER_POOL_ID, Username=owner)
            attributes = {a['Name']: a['Value'] for a in response.get('UserAttributes', [])}
            email = email or attributes.get('email', '')
            given_name = attributes.get('given_name', '').strip()
            full_name = attributes.get('name', '').strip()
            if not full_name:
                full_name = ' '.join(
                    x for x in (given_name, attributes.get('family_name', '').strip()) if x
                )
        except Exception as e:
            logger.warning(f"Could not resolve Cognito user {owner}: {str(e)}")

    # Ownership: the username, never a display value.
    lma_user = owner
    first_name = ''

    # Prefer the directory's given name, but take only its first word: Entra
    # populates givenName from AD, where it often carries middle names too
    # ("Bala Yogeswara Singh"). Roughly a fifth of the pool has no given_name
    # at all, so fall back to the email's local part, which every user has.
    if given_name:
        first_name = given_name.split()[0]
    elif '@' in email:
        first_name = email.split('@')[0].split('.')[0].split('_')[0]

    # Some directory records are stored lowercase. Only lift the first letter,
    # so names that are intentionally mixed-case ("McDonald") survive.
    if first_name:
        first_name = first_name[:1].upper() + first_name[1:]

    if first_name:
        lma_identity = "{}'s AI Assistant".format(first_name)
    else:
        # Nothing usable resolved - keep the task definition's original format
        # rather than joining with a bare or empty name.
        lma_identity = 'LMA ({})'.format(lma_user)

    # Substitute here rather than letting the container expand {LMA_USER}, so the
    # person's name reaches the intro without going through LMA_USER.
    speaker = full_name or email or lma_user
    intro_message = INTRO_MESSAGE_TEMPLATE.replace('{LMA_USER}', speaker)

    return lma_user, lma_identity, intro_message

def lambda_handler(event, context):
    logger.info("Processing VP scheduler request")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        for record in event.get('Records', []):
            event_name = record.get('eventName')
            dynamodb_data = record.get('dynamodb', {})
            
            keys = dynamodb_data.get('Keys', {})
            vp_id = keys.get('id', {}).get('S')
            
            if not vp_id:
                logger.warning("No VP ID found in record, skipping")
                continue
                
            logger.info(f"Processing {event_name} event for VP {vp_id}")
            
            if event_name == 'INSERT':
                new_image = dynamodb_data.get('NewImage', {})
                is_scheduled = new_image.get('isScheduled', {}).get('BOOL', False)
                meeting_time = new_image.get('meetingTime', {}).get('N')
                
                if is_scheduled and meeting_time:
                    meeting_time_int = int(meeting_time)
                    current_time = int(datetime.now().timestamp())
                    
                    # Schedule for future execution
                    delay_seconds = 120  # 2 minutes before meeting
                    scheduled_time = datetime.fromtimestamp(meeting_time_int - delay_seconds, tz=timezone.utc)
                    
                    lma_user, lma_identity, intro_message = resolve_vp_identity(new_image)

                    ecs_params = {
                        'ClientToken': vp_id,
                        'TaskDefinition': os.environ['TASK_DEFINITION_ARN'],
                        'Cluster': os.environ['CLUSTER_ARN'],
                        'LaunchType': os.environ.get('VP_LAUNCH_TYPE', 'FARGATE'),
                        'NetworkConfiguration': {
                            'AwsvpcConfiguration': {
                                'AssignPublicIp': 'DISABLED',
                                'SecurityGroups': json.loads(os.environ['SECURITY_GROUPS']),
                                'Subnets': json.loads(os.environ['SUBNETS']),
                            }
                        },
                        'Overrides': {
                            'ContainerOverrides': [{
                                'Name': os.environ['CONTAINER_NAME'],
                                'Environment': [
                                    {'Name': 'VIRTUAL_PARTICIPANT_ID', 'Value': vp_id},
                                    {'Name': 'MEETING_PLATFORM', 'Value': new_image.get('meetingPlatform', {}).get('S', '')},
                                    {'Name': 'MEETING_ID', 'Value': new_image.get('meetingId', {}).get('S', '')},
                                    {'Name': 'MEETING_PASSWORD', 'Value': new_image.get('meetingPassword', {}).get('S', '')},
                                    {'Name': 'MEETING_NAME', 'Value': new_image.get('meetingName', {}).get('S', '')},
                                    {'Name': 'MEETING_TIME', 'Value': str(meeting_time_int)},
                                    {'Name': 'LMA_USER', 'Value': lma_user},
                                    # Join name. Overrides the task definition's "LMA ({LMA_USER})".
                                    # Contains no {LMA_USER} placeholder, so the container uses it
                                    # verbatim. Safe only because the image verifies what it types -
                                    # Teams drops keystrokes while its prejoin field hydrates.
                                    {'Name': 'LMA_IDENTITY', 'Value': lma_identity},
                                    # Pre-expanded, so the container has no {LMA_USER} left to
                                    # substitute and the spoken intro names the person.
                                    {'Name': 'INTRO_MESSAGE', 'Value': intro_message},
                                    {'Name': 'GRAPHQL_ENDPOINT', 'Value': os.environ.get('GRAPHQL_ENDPOINT', '')},
                                    {'Name': 'CALL_DATA_STREAM_NAME', 'Value': os.environ.get('CALL_DATA_STREAM_NAME', '')},
                                    {'Name': 'RECORDINGS_BUCKET_NAME', 'Value': os.environ.get('RECORDINGS_BUCKET_NAME', '')},
                                    {'Name': 'VP_TASK_REGISTRY_TABLE_NAME', 'Value': os.environ.get('VP_TASK_REGISTRY_TABLE_NAME', '')},
                                ]
                            }]
                        },
                        'EnableExecuteCommand': False
                    }
                    
                    schedule_expression = f"at({scheduled_time.strftime('%Y-%m-%dT%H:%M:%S')})"
                    
                    response = scheduler_client.create_schedule(
                        ActionAfterCompletion='DELETE',
                        FlexibleTimeWindow={'Mode': 'OFF'},
                        GroupName=os.environ['SCHEDULE_GROUP_NAME'],
                        Name=vp_id,
                        ScheduleExpression=schedule_expression,
                        ScheduleExpressionTimezone='UTC',
                        State='ENABLED',
                        Target={
                            'Arn': 'arn:aws:scheduler:::aws-sdk:ecs:runTask',
                            'RoleArn': os.environ['SCHEDULER_ROLE_ARN'],
                            'Input': json.dumps(ecs_params)
                        }
                    )
                    
                    logger.info(f"Successfully scheduled VP {vp_id} with schedule ARN: {response.get('ScheduleArn')}")
                else:
                    logger.info(f"VP {vp_id} is not scheduled, skipping")
                    
            elif event_name == 'REMOVE':
                try:
                    scheduler_client.delete_schedule(
                        GroupName=os.environ['SCHEDULE_GROUP_NAME'],
                        Name=vp_id
                    )
                    logger.info(f"Successfully unscheduled VP {vp_id}")
                except scheduler_client.exceptions.ResourceNotFoundException:
                    logger.info(f"Schedule for VP {vp_id} not found (may have already been executed)")
                except Exception as e:
                    logger.error(f"Error unscheduling VP {vp_id}: {str(e)}")
        
        return {'statusCode': 200, 'body': json.dumps({'message': 'Successfully processed VP scheduler events'})}
        
    except Exception as e:
        logger.error(f"Error processing VP scheduler events: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
