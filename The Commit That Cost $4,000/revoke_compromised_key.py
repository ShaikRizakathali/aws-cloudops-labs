import boto3
import json

iam = boto3.client('iam')

def lambda_handler(event, context):
    print("Received event: " + json.dumps(event))

    detail = event.get('detail', {})
    resource = detail.get('resource', {})
    access_key_details = resource.get('accessKeyDetails', {})

    user_name = access_key_details.get('userName')
    access_key_id = access_key_details.get('accessKeyId')

    if not user_name or not access_key_id:
        print("No IAM access key details in this finding — nothing to remediate.")
        return {
            'statusCode': 200,
            'body': 'No access key found in finding, no action taken'
        }

    print(f"Deactivating access key {access_key_id} for user {user_name}")

    iam.update_access_key(
        UserName=user_name,
        AccessKeyId=access_key_id,
        Status='Inactive'
    )

    print(f"Access key {access_key_id} deactivated.")

    return {
        'statusCode': 200,
        'body': json.dumps(f'Deactivated access key {access_key_id} for user {user_name}')
    }
