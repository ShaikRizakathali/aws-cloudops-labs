import boto3
import json

ssm = boto3.client('ssm')

INSTANCE_ID = 'i-xxxxxxxxxxxxxxxxx'  # replace with your instance ID

def lambda_handler(event, context):
    print("Received event: " + json.dumps(event))

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={
            'commands': [
                'BEFORE=$(df -h / | tail -1)',
                'echo "Before cleanup: $BEFORE"',
                'truncate -s 0 /var/log/fakeapp/app.log',
                'echo "Log truncated by auto-remediation Lambda at $(date)" >> /var/log/fakeapp/app.log',
                'AFTER=$(df -h / | tail -1)',
                'echo "After cleanup: $AFTER"'
            ]
        }
    )

    command_id = response['Command']['CommandId']
    print(f"Sent SSM command {command_id} to instance {INSTANCE_ID}")

    return {
        'statusCode': 200,
        'body': json.dumps(f'Remediation command sent: {command_id}')
    }
