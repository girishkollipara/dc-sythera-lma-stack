import os
import json
import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

scheduler_client = boto3.client('scheduler')
ecs_client = boto3.client('ecs')

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
                                    {'Name': 'LMA_USER', 'Value': new_image.get('owner', {}).get('S', '')},
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
