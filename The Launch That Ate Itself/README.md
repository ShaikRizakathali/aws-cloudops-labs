# Project 5: The Launch That Ate Itself

**Services:** EC2 (Launch Template + Auto Scaling Group) · Application Load Balancer · CloudWatch (target-tracking + custom alarm) · SNS

## The Problem

Marketing sends the launch email. Traffic climbs. By the time anyone notices,
the single EC2 instance behind the app is pegged at 100% CPU, response times
have gone from 80ms to 12 seconds, and then it just stops responding. No load
balancer. No auto-scaling. Someone built this as a quick demo eight months ago
and it quietly became "the production app."

This project builds the thing that should have existed from day one: traffic
spread across multiple instances behind a load balancer, automatic scaling
that reacts to real load instead of a human noticing late, and self-healing
that replaces a broken instance before anyone has to SSH in and restart it
by hand.

## Repo Contents

- `scripts/user-data.sh` — launch template bootstrap script
- `config/` — security group rules, target group health check, the scaling
  policy, and the corrected CloudWatch alarm, each as JSON with notes on what
  changed and why
- `screenshots/` — the evidence referenced throughout this README, numbered
  in the order things actually happened, bugs included

## What This Solves

- Traffic is distributed across instances instead of hitting one box
- The group scales out automatically when average CPU climbs, and back in
  when it settles
- A failed health check gets an instance pulled from rotation and replaced
  automatically — no manual intervention
- State changes actually page someone, in time to matter

## Architecture

```
                              Internet
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │  Application Load        │
                    │  Balancer (ALB)          │
                    │  (public subnet, HTTP)   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Target Group            │
                    │   (health check: /)       │
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
      ┌───────▼──────┐  ┌────────▼─────┐   ┌────────▼─────┐
      │  EC2 Instance │  │ EC2 Instance │   │ EC2 Instance │
      │  (ASG member) │  │ (ASG member) │   │ (added under │
      │  min:1 max:4  │  │              │   │   load)       │
      └───────┬───────┘  └──────┬───────┘   └──────┬───────┘
              │                  │                  │
              └──────────────────┴──────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  CloudWatch Alarms        │
                    │  - Target-tracking CPU    │
                    │    (scale out / in)       │
                    │  - Unhealthy-host alarm   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │      SNS Topic            │
                    │  (asg-health-alerts)      │
                    └───────────────────────────┘
```

**Why an ALB over DNS-based load balancing?** DNS round-robin has no idea
when a target is unhealthy — it keeps sending traffic to a dead server until
a TTL expires. The ALB actively health-checks every 15 seconds and pulls a
bad target out of rotation almost immediately.

**Why security groups reference each other instead of CIDR blocks?** The
instance security group only accepts HTTP from the ALB's security group by
ID, not from `0.0.0.0/0`. Nobody can bypass the load balancer and hit an
instance directly, even if they find its public IP.

**Weakest link:** The launch template's user-data script. If the app doesn't
reliably start on every fresh instance the same way, every time, auto-scaling
just gives you more broken servers, faster.

## Bug #1 — The Scaling Policy That Never Existed

I built the ASG through the console wizard, including a target-tracking
scaling policy step, and moved on. First load test: `stress --cpu 2
--timeout 600` on the instance, a full 10 minutes pegging both vCPUs. I
expected a scale-out. Nothing happened — Activity history showed only the
original launch event, ever.

My first guess was wrong. I assumed the instance type (`t2.micro` instead of
the `t3.micro` I'd planned) was throttling CPU via the credit system, and
that CloudWatch was seeing a throttled, lower number than what `stress` was
actually asking for.

I checked the actual `CPUUtilization` metric for that instance during the
test window before accepting that theory. It hit **99.71%** — real,
unthrottled, sustained load (`screenshots/06-cpu-not-throttled-99pct.png`).
T2 credits were never the issue.

Then I checked the ASG's **Automatic scaling** tab directly:
**zero dynamic scaling policies existed** (`screenshots/05-bug1-no-scaling-policy.png`).
The policy step in the creation wizard simply hadn't saved — an easy, silent
miss. I created the policy manually after the fact
(`screenshots/07-scaling-policy-created-fix.png`), re-ran the exact same
stress test, and this time got a real event:

> *"a monitor alarm TargetTracking-webapp-asg-AlarmHigh... in state ALARM
> triggered Target Tracking Policy changing the desired capacity from 1 to
> 2"* — `screenshots/08-proof-real-scaleout-event.png`

**Lesson:** don't trust that a wizard step saved just because it didn't
error. Check the actual resource it was supposed to create.

## Bug #2 — The Alarm That Got Outrun by Its Own Automation

Separately, I stopped Apache on an instance to test the unhealthy-host alarm
and confirm self-healing. The ASG caught it fine — target marked unhealthy,
instance terminated, replacement launched
(`screenshots/09-proof-self-healing-replace.png`). But no email ever
arrived.

Checking the alarm's History tab: it had **never once transitioned to
`ALARM`** since creation, despite a real incident just having happened
(`screenshots/10-bug2-alarm-never-fired.png`).

The alarm was configured for `UnHealthyHostCount >= 1` across **2
consecutive 1-minute datapoints**. But the target group's own health check
was aggressive — a 15-second interval with a 2-check unhealthy threshold —
so the ALB could mark a target unhealthy in as little as 30 seconds, and the
ASG reacted almost immediately after that. The instance was gone before a
second full minute of "unhealthy" data ever accumulated for the alarm to
confirm against.

**My own self-healing automation was faster than my own monitoring** —
which meant a real incident could happen and resolve itself completely
silently, with nobody ever paged.

Fix: dropped "Datapoints to alarm" from 2/2 to **1/1**
(`screenshots/11-bug2-threshold-fixed.png`). Re-ran the same test. This time
the alarm actually fired and the email actually arrived
(`screenshots/12-proof-real-alarm-email.png`,
`screenshots/13-proof-alarm-in-alarm-state.png`).

**Lesson:** fast automation isn't automatically a good thing if it's fast
enough to hide the incident from your own monitoring. Alerting thresholds
need to be evaluated against how quickly *other* automation in the same
system reacts, not just against what looks like a reasonable default.

## Test Evidence

Full sequence, in order, all with matching timestamps in the AWS console:

1. Security groups correctly chained (ALB SG referenced by ID, not CIDR) —
   `screenshots/01-security-groups-chained.png`
2. ALB active, listener forwarding to target group —
   `screenshots/02-alb-active-listener.png`
3. Target group healthy, ELB health checks enabled on the ASG with a 60s
   grace period — `screenshots/03-target-group-healthy.png`,
   `screenshots/04-asg-elb-healthcheck-config.png`
4. Real CPU-based scale-out, triggered by an actual CloudWatch alarm
   breach — `screenshots/08-proof-real-scaleout-event.png`
5. Real self-healing replacement after a manually induced failure —
   `screenshots/09-proof-self-healing-replace.png`
6. Real unhealthy-host alarm firing and a real SNS email landing —
   `screenshots/12-proof-real-alarm-email.png`

## Cost (24 hours, baseline)

| Resource | Cost |
|---|---|
| ALB | ~$0.60-0.80/day even fully idle — not Free Tier, charges regardless of traffic |
| EC2 t2.micro (1 instance baseline) | Free Tier eligible |
| Auto Scaling | No charge — only pay for the EC2 instances it launches |
| CloudWatch alarms | First 10 free |
| SNS | Free under 1,000 emails |

**Biggest risk:** forgetting the ALB is running. It bills whether or not
anything hits it. If a load test pushes the group to max (4 instances) and
you forget to scale back down, that's the multiplier — cheap on t2.micro,
but still worth not leaving unattended.

## What I'd Do Differently in Production

- Never assume a console wizard step saved correctly — verify the actual
  resource, not just the absence of an error message
- Set alarm evaluation windows *relative to* how fast other automation in
  the same system can react, not against a generic default
- Add a second, independent detection path (e.g. a composite alarm or a
  separate monitoring tool) so alerting doesn't depend on a single
  CloudWatch alarm configuration being right the first time

## Cleanup

1. EC2 → Auto Scaling Groups → delete `webapp-asg` (terminates all instances
   in the group automatically)
2. EC2 → Target Groups → delete `webapp-tg`
3. EC2 → Load Balancers → delete `webapp-alb`
4. EC2 → Launch Templates → delete `webapp-launch-template`
5. CloudWatch → Alarms → delete `webapp-unhealthy-hosts` (the target-tracking
   alarms are removed automatically when the ASG is deleted)
6. SNS → delete the subscription, then the topic `asg-health-alerts`
7. EC2 → Security Groups → delete `webapp-instance-sg` **first**, then
   `alb-sg` (order matters — one references the other)
8. EC2 → Key Pairs → delete `cloudops-lab-key` if not reused elsewhere
