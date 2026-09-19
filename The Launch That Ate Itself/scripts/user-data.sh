#!/bin/bash
yum update -y
yum install -y httpd stress
systemctl start httpd
systemctl enable httpd

TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
AZ=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/availability-zone)

cat <<EOF > /var/www/html/index.html
<html>
<head><title>CloudOps Lab - Scaling Test</title></head>
<body style="font-family: monospace; background:#111; color:#0f0; padding:40px;">
<h1>Served by instance: $INSTANCE_ID</h1>
<h2>Availability Zone: $AZ</h2>
</body>
</html>
EOF
