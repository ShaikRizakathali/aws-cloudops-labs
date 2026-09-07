#!/bin/bash
yum update -y
yum install -y amazon-cloudwatch-agent cronie
systemctl enable --now crond

mkdir -p /var/log/fakeapp

cat << 'EOF' > /etc/cron.d/disk-filler
* * * * * root head -c 20M </dev/urandom >> /var/log/fakeapp/app.log 2>/dev/null
EOF

mkdir -p /opt/aws/amazon-cloudwatch-agent/etc
cat << 'EOF' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
{
  "metrics": {
    "namespace": "CustomEC2Metrics",
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent"],
        "resources": ["/"],
        "metrics_collection_interval": 60
      },
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 60
      }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s
