# Project 4: The Database That Ghosted Us

**Services:** RDS (MySQL) · Lambda · EventBridge · SNS · IAM

## The Problem

A production RDS instance goes to "stopped" and nobody notices — someone manually
stopped it during testing and forgot, or it hit AWS's 7-day auto-stop timeout.
The app throws 500s. The automated backups exist, but nobody has ever actually
tested restoring from one. That's a fire drill nobody wants to run for the first
time during an actual incident.

This project builds the protection layer around that scenario: automated
detection and recovery when RDS stops unexpectedly, a scheduled backup job that
doesn't depend on anyone remembering to run it, alerting the moment something
changes state, and a **tested** restore procedure — not a theoretical one.

## What This Solves

- Database gets stopped (accidentally or by AWS auto-stop) → it auto-restarts
  without a human in the loop
- Backups happen on a schedule, independent of anyone remembering
- Every state change (stopped, failed, backing up, recovered) sends an email
  alert
- The restore procedure has actually been run and verified, not just assumed
  to work

## Architecture

```
                    ┌─────────────────────────────┐
                    │        RDS MySQL Instance    │
                    │   (db-cloudops-lab, Free Tier│
                    │    Single-AZ, us-east-1)     │
                    └────────────┬────────────────┘
                                 │
                    RDS Event Notifications
                                 │
                    ┌────────────▼────────────┐
                    │    SNS Topic             │
                    │  (rds-state-alerts)      │
                    └──────┬──────────────────┘
                           │                  │
                    Email Sub         ┌───────▼──────────┐
                    (alerts)          │  EventBridge Rules│
                                      │                   │
                           ┌──────────┘         ┌────────┘
                           │                    │
              ┌────────────▼──────┐  ┌──────────▼──────────────┐
              │ Lambda #1         │  │ Lambda #2               │
              │ auto-start-rds    │  │ create-rds-snapshot     │
              │ (triggers on any  │  │ (scheduled daily,       │
              │  RDS instance     │  │  01:00 UTC)             │
              │  event; checks    │  └─────────────────────────┘
              │  for "stopped" in │
              │  the message)     │
              └───────────────────┘
```

**Why RDS and not Aurora?** Aurora Serverless v2 isn't Free Tier and starts at
~$0.12/ACU-hour. For a lab proving database ops fundamentals, `db.t3.micro`
covers the same concepts at a fraction of the cost.

**Why Single-AZ?** Because that's what gets spun up when someone's cutting
corners in dev/test, and that's the exact failure mode this project defends
against. Multi-AZ solves hardware failure — it does nothing for "someone
manually stopped it."

**Weakest link:** The Lambda IAM role. Scoped to one DB's ARN specifically —
if that scope were `Resource: "*"` instead, a bug in this automation could
restart or snapshot *any* RDS instance in the account, not just this lab.

**What happens if Lambda fails?** The SNS alert still fires — it comes
directly from the RDS event subscription, not from Lambda. Lambda failing
means no auto-remediation, but you still get paged. Alerting and automation
are deliberately on separate paths.

## The Bug I Hit and How I Found It

My first EventBridge rule filtered on `EventCategories: ["availability"]` in
addition to `SourceIdentifier`. It matched AWS's own documentation examples
and looked correct. After manually stopping the DB, nothing fired — no Lambda
invocation, no CloudWatch log group even got created for the function.

I isolated the problem by invoking the Lambda manually with a synthetic
"stopped" event through the console Test tab. That succeeded and actually
issued a real `start_db_instance` call — which confirmed the Lambda logic
itself was fine. The problem was upstream, in the EventBridge rule not
matching the real event at all.

I removed the `EventCategories` filter from the rule entirely and kept only
`SourceIdentifier`, moving the "is this actually a stopped event" check into
the Lambda's own code instead of the EventBridge pattern. After that change,
the rule matched immediately on the next real stop.

**The part I almost got wrong:** my first "successful" test after the fix
was actually a false positive. The DB was already mid-recovery from an
earlier manual Lambda test, and what I was watching in CloudWatch was that
leftover recovery sequence playing out — not a fresh trigger from a genuine
console-initiated stop. I caught this by checking the exact timestamp of the
"Start command issued" log line against the exact moment I clicked Stop in
the console. They didn't line up. I re-ran the test cleanly: started tailing
the Lambda's CloudWatch logs *before* stopping the DB, then stopped it, and
watched a brand-new, unambiguous invocation appear in real time with a
timestamp matching the stop to the second.

## Test Evidence

Live Tail output from a clean test — DB stopped manually in the console with
CloudWatch Logs already tailing, no manual Lambda invocation involved:

```
2026-09-18T03:03:58.620Z  Received event: {"version": "0", "id": "749a25c5-...", "detail-type": "RDS DB Instance Event", "source": "aws.rds", ...}
2026-09-18T03:03:58.620Z  Source: db-cloudops-lab, Message: DB instance stopped
2026-09-18T03:03:59.000Z  Instance is stopped. Attempting to start db-cloudops-lab...
2026-09-18T03:03:59.609Z  Start command issued successfully.
2026-09-18T03:04:22.497Z  Source: db-cloudops-lab, Message: Recovery of the DB instance has started. Recovery time will vary with the amount of data to be recovered.
```

From stop event to issued start command: **under 2 seconds**, with zero
manual intervention.

## Restore Test

Automated backups and manual snapshots only count for something if the
restore has actually been proven to work:

1. Took a manual snapshot (`manual-restore-test-01`)
2. Restored it to a separate instance (`db-cloudops-lab-restored`)
3. Confirmed the restored instance came up `Available` with the `labdb`
   database present
4. Deleted the restored instance immediately after verification (restore
   defaults to a larger, non-Free-Tier instance class — check and correct
   this before leaving it running)

## Cost (24 hours)

| Resource | Cost |
|---|---|
| RDS db.t3.micro | ~$0.41 (Free Tier eligible) |
| RDS storage (20GB) | Free Tier covered |
| Manual snapshots | Negligible short-term; **delete on cleanup, they accumulate** |
| Lambda | Free Tier covered |
| SNS | Free Tier covered (first 1,000 emails) |
| EventBridge | Free Tier covered (first 14M events/month) |

**Biggest risk:** forgetting to delete the restore-test instance. It restores
to a larger instance class by default (I got `db.m7g.large` — check the
"Burstable classes" toggle to find `t3.micro` in the restore flow) and two
instances billing simultaneously adds up fast if left running.

## What I'd Do Differently in Production

- Multi-AZ for actual hardware failure protection — this project only
  covers "someone/something stopped the instance," not physical failure
- A CloudWatch alarm as a second, independent layer on top of the RDS event
  notification, so alerting doesn't depend on a single delivery path
- Make the restore-instance-class check a required, non-skippable step —
  I hit this by accident and it's an easy way to burn money without
  noticing

## Cleanup Checklist

1. Delete any restored test instances
2. Delete manual snapshots (RDS → Snapshots → Manual)
3. Delete the RDS instance (uncheck "create final snapshot" if you don't
   need it)
4. Delete both EventBridge rules
5. Delete both Lambda functions
6. Delete the SNS subscription, then the topic
7. Delete the RDS event subscription
8. Delete the IAM role
9. Delete the security group (only after the RDS instance is gone)
