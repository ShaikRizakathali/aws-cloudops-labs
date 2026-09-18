import boto3
import json
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client('rds', region_name='us-east-1')
DB_INSTANCE_ID = 'db-cloudops-lab'

def lambda_handler(event, context):
    logger.info(f"Snapshot job triggered at {datetime.utcnow().isoformat()}")

    # Check instance status first — can't snapshot a stopped instance
    try:
        response = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
        status = response['DBInstances'][0]['DBInstanceStatus']
        logger.info(f"Current DB status: {status}")

        if status != 'available':
            logger.warning(f"DB is {status}, not available. Skipping snapshot.")
            return {
                'statusCode': 200,
                'body': f'Snapshot skipped — DB status is {status}'
            }
    except Exception as e:
        logger.error(f"Failed to describe DB instance: {str(e)}")
        raise e

    # Generate snapshot ID with timestamp
    timestamp = datetime.utcnow().strftime('%Y-%m-%d-%H%M')
    snapshot_id = f"manual-{DB_INSTANCE_ID}-{timestamp}"

    try:
        logger.info(f"Creating snapshot: {snapshot_id}")
        response = rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=DB_INSTANCE_ID,
            Tags=[
                {'Key': 'CreatedBy', 'Value': 'Lambda'},
                {'Key': 'Project', 'Value': 'cloudops-lab'},
                {'Key': 'Type', 'Value': 'manual-scheduled'}
            ]
        )

        snap_status = response['DBSnapshot']['Status']
        logger.info(f"Snapshot {snapshot_id} initiated. Status: {snap_status}")

        return {
            'statusCode': 200,
            'body': f'Snapshot {snapshot_id} created. Status: {snap_status}'
        }

    except Exception as e:
        logger.error(f"Snapshot creation failed: {str(e)}")
        raise e
