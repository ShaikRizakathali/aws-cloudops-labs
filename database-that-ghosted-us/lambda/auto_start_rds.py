import boto3
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client('rds', region_name='us-east-1')
DB_INSTANCE_ID = 'db-cloudops-lab'

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    # EventBridge delivers RDS events in the detail field
    detail = event.get('detail', {})
    message = detail.get('Message', '')
    source_id = detail.get('SourceIdentifier', '')

    logger.info(f"Source: {source_id}, Message: {message}")

    # Only act on our specific instance
    if source_id != DB_INSTANCE_ID:
        logger.info(f"Event not for {DB_INSTANCE_ID}, ignoring.")
        return {'statusCode': 200, 'body': 'Not our instance'}

    # RDS stopped events have this message
    if 'stopped' in message.lower() or 'DB instance stopped' in message:
        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
            status = response['DBInstances'][0]['DBInstanceStatus']

            if status == 'stopped':
                logger.info(f"Instance is stopped. Attempting to start {DB_INSTANCE_ID}...")
                rds.start_db_instance(DBInstanceIdentifier=DB_INSTANCE_ID)
                logger.info("Start command issued successfully.")
                return {'statusCode': 200, 'body': f'Start command sent for {DB_INSTANCE_ID}'}
            else:
                logger.info(f"Instance status is {status}, no action needed.")
                return {'statusCode': 200, 'body': f'Instance status: {status}'}

        except Exception as e:
            logger.error(f"Failed to start instance: {str(e)}")
            raise e
    else:
        logger.info(f"Message doesn't indicate stopped state: {message}")
        return {'statusCode': 200, 'body': 'No action taken'}
