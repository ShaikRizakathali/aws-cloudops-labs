# Project 2: The Commit That Cost $4,000

**Services:** GuardDuty · EventBridge · SNS · Lambda (Python/boto3) · IAM

## The Problem

Somewhere out there, right now, there's a bot that does nothing but scrape every public GitHub commit for strings that look like an AWS access key ID. The second one shows up in a public repo, that bot's got it, and within minutes something's spinning up instances in a region I've never opened the console for — not to steal data, to mine crypto on my dime while I'm asleep.

I'd just spent the whole previous project pushing real commits to a real public repo. Nothing leaked, but that's exactly the moment this problem bites people in real life — mid-flow, moving fast, one `git add .` away from a credentials file getting swept up by accident.

Project 1 watched a number cross a threshold. This is different — nobody sets a percentage on "an attacker is using your key." That's a pattern of behavior, not a metric. Different problem, different tool.

## What We Build

GuardDuty sits on top of CloudTrail activity and uses anomaly detection to spot things that look like a compromised credential. When it finds something, it doesn't trip an alarm the way CloudWatch does — it publishes a structured event. So the fan-out point here is **EventBridge**, matching on the *shape* of an event instead of a number crossing a line, filtered to anything High severity or above. That routes two ways: SNS pages a human by email, and a Lambda function deactivates the exact compromised access key — not the whole IAM user, just that one key, stopped cold, evidence trail intact.

I did not leak a real key to test this. GuardDuty ships a "generate sample findings" button for exactly this reason, and I used that plus a disposable decoy IAM user to prove the remediation logic separately from the detection pipeline.

## Architecture

```
   Leaked/compromised
   IAM access key used
   somewhere it shouldn't be
            │
            v
    ┌─────────────────┐
    │    GuardDuty      │  (continuously analyzes CloudTrail
    │   (the detector)  │   management events, no setup needed)
    └────────┬──────────┘
             │ finding published as an event
             v
    ┌──────────────────────┐
    │   EventBridge Rule     │  source: aws.guardduty
    │                        │  filter: severity >= 7
    └────────┬──────────┬────┘
             │          │
    ┌────────v──┐   ┌───v──────────────┐
    │   SNS      │   │     Lambda        │
    │ (pages you)│   │ (auto-remediate) │
    └────────────┘   └───────┬──────────┘
                              │ iam:UpdateAccessKey
                              v
                   ┌────────────────────┐
                   │  Compromised access │
                   │  key → Inactive     │
                   └────────────────────┘
```

**Why GuardDuty, not hand-rolled CloudTrail parsing** — reinventing anomaly detection across billions of events is not a fight worth picking against a managed service already built for exactly this.

**Why EventBridge, not a CloudWatch Alarm like Project 1** — Alarms watch a number against a threshold. A GuardDuty finding is a structured JSON event with no number to threshold. EventBridge pattern-matches on the *shape* of the event instead.

**Why deactivate the specific key instead of nuking the whole IAM user** — minimum blast radius. Delete the user and you might kill other valid keys, break automation depending on it, and lose the audit trail right when you need it. Flipping one key to Inactive stops the bleeding while leaving the evidence for a human to review.

**Weakest link** — this contains damage, it doesn't explain it. It doesn't rotate anything, doesn't scan repos, doesn't tell you how the key leaked. A human still does the actual investigation.

## Cost

GuardDuty's first-time-enabled 30-day free trial covers this project fully. Past the trial, foundational detection runs about $4 per million CloudTrail management events — a personal lab account generating a few thousand events a day lands under a cent. EventBridge doesn't charge for AWS-service-sourced events on the default bus. SNS and Lambda are nowhere near their free thresholds at this scale. Nothing here runs 24/7 racking up an hourly rate the way the EC2 box in Project 1 did — the real cost risk this project defends against is a genuinely leaked key running unattended for hours, which routinely runs into real bills, not the lab itself.

## Build

Lambda code: [`lambda/revoke_compromised_key.py`](./lambda/revoke_compromised_key.py)
IAM policy for the Lambda's execution role: [`lambda/revoke_compromised_key_policy.json`](./lambda/revoke_compromised_key_policy.json)
EventBridge event pattern: [`eventbridge-event-pattern.json`](./eventbridge-event-pattern.json)

Short version: enabled GuardDuty in us-east-1, created a disposable decoy IAM user (`lab-decoy-user`) with one access key purely as a remediation test target, built the Lambda and its execution role, built an EventBridge rule matching `aws.guardduty` findings at severity >= 7 with two targets (SNS + Lambda), and an SNS topic (`security-incident-alerts`) with an email subscription.

## What Actually Broke During the Build

This project had more real bugs than Project 1, and they cluster around one root cause showing up from different angles: **IAM permissions answer "what can this identity do," and every one of these bugs came from mixing up who the acting identity actually is.**

### Bug 1 — an EventBridge target using the wrong role

Configuring the SNS target, the "Execution role" dropdown pre-filled with the Lambda's own execution role — a role scoped to exactly one thing, `iam:UpdateAccessKey` on the decoy user. It had zero permission to publish to SNS. Console would've shown everything green; the SNS target would've failed silently on every real trigger. Fix: click "Create new role" instead of reusing what was in the dropdown.

### Bug 2 — a resource name sitting where an API action belonged

```json
"Action": ["sns:security-incident-alerts"]
```

`Action` answers *which API call*, always `service:Operation` — `sns:Publish`, not a topic name. That belongs in `Resource`, which was already correct two lines down. IAM's own validator flagged this before it ever got saved.

![Wrong value in the Action field of an IAM policy](./screenshots/01-wrong-action-in-sns-policy-bug.png)

### Bug 3 — a permission attached to the object instead of the actor

The `iam:UpdateAccessKey` policy ended up attached directly to `lab-decoy-user` — the passive target sitting in the API call's `Resource` field, not the thing making the call. IAM permissions attach to the actor calling the API (the Lambda's execution role), never to the object being acted on. This produced a real, confirmable `AccessDenied` error naming the actual role that needed the grant.

![Permission attached to the decoy user instead of the Lambda's role](./screenshots/02-permission-attached-wrong-principal.png)

![The resulting AccessDenied error from Lambda's test invoke](./screenshots/03-lambda-test-accessdenied.png)

Confirmed fixed by moving the policy to the correct role and re-testing — the decoy key flipped to Inactive.

![Decoy access key confirmed Inactive after a correct remediation run](./screenshots/04-decoy-key-deactivated-proof.png)

### Bug 4 — a "successful" test that quietly regressed

Retested later in the session and hit the exact same `AccessDenied` error again — the permission had somehow disappeared between tests. Root cause turned out to be Bug 6 below; the grant wasn't gone, it was hiding under a misleading name.

### Bug 5 — SNS subscriptions silently dying after confirmation

Confirmed an email subscription, and within seconds it flipped to status `Deleted` with an automatic "Unsubscribe Confirmation" email following right behind — with no action taken on my end.

![SNS subscription status shows Deleted despite a Confirmed badge elsewhere](./screenshots/05-sns-subscription-silently-deleted.png)

Root cause: SNS confirmation and unsubscribe links are unauthenticated public URLs — anyone (or anything) that loads the link can trigger them, no AWS login required. Mail security scanners commonly pre-fetch every link in an incoming email to check it's safe, which silently fires the unsubscribe before a human ever sees the message. This is documented and unauthenticated Unsubscribe/ConfirmSubscription calls are explicitly **not logged in CloudTrail**, which I confirmed directly — checking Event History for `Unsubscribe` came back with zero matches, consistent with an anonymous link click rather than proof nothing happened.

![Automatic unsubscribe-confirmation email arriving seconds after confirming the subscription](./screenshots/08-mail-scanner-auto-unsubscribe.png)

**Real fix, not applied in this lab run but worth doing in production:** confirm the subscription manually via the AWS CLI with `--authenticate-on-unsubscribe true`, which makes any future unauthenticated unsubscribe attempt fail outright:

```bash
aws sns confirm-subscription \
  --topic-arn arn:aws:sns:REGION:ACCOUNT_ID:security-incident-alerts \
  --token TOKEN_FROM_THE_CONFIRMATION_EMAIL_LINK \
  --authenticate-on-unsubscribe true
```

### Bug 6 — a policy named for one thing, granting another

The Lambda's execution role showed two policies both named `AWSLambdaBasicExecutionRole` at a glance. The second one — a customer-managed policy that just happened to share the standard name — actually contained the real `iam:UpdateAccessKey` grant the whole time. The permission was never missing; it was mislabeled in a way that made it look like harmless logging boilerplate. This is the same lesson as "policy names lie, JSON doesn't" from Project 1, but sharper: here the label was misleading in the dangerous direction, not just uninformative.

![A policy named like the standard logging role, actually containing an IAM key-revocation grant](./screenshots/09-misleadingly-named-policy-discovery.png)

### Bug 7 — Lambda's own execution role missing CloudWatch Logs permission

Separate, unrelated issue: the Lambda console flagged that its execution role couldn't write to CloudWatch Logs at all, despite earlier manual tests showing log output. Somewhere across the role editing this session, the basic logging policy attachment appears to have been affected. Fixed by re-attaching the `AWSLambdaBasicExecutionRole` managed policy directly.

![Lambda console flagging missing CloudWatch Logs write permission](./screenshots/07-lambda-missing-cloudwatch-logs-permission.png)

## Testing & Proof

Confirmed at every stage, not just the end result:

- **GuardDuty** generated real sample findings at severity 8, including `CredentialAccess:IAMUser/CompromisedCredentials` — the exact scenario this project is built around.

![GuardDuty findings list showing High and Critical severity sample findings](./screenshots/06-guardduty-high-severity-samples.png)

- **EventBridge** matched severity >= 7 events and invoked both targets — confirmed via the rule's own Monitoring tab showing matched/invocation counts.
- **Lambda's remediation logic**, tested independently against a real decoy access key (not a GuardDuty placeholder identity, which doesn't correspond to anything real in the account) — confirmed the key flipped to Inactive in the IAM console after invocation.
- **SNS actually delivered a real, automatically-triggered alert email** — not a manual test, a genuine end-to-end firing containing the real finding JSON:

![Real GuardDuty finding delivered by email — IAM access key flagged as compromised, severity 8](./screenshots/10-real-alert-delivered-compromised-credentials.png)

Every stage confirmed independently before trusting the next one — a green console checkmark was never treated as proof by itself anywhere in this build.

## Common Failures

- **EventBridge rule never fires** — check the event bus is `default`, and that sample findings were generated in the same region the rule lives in.
- **Rule matches everything or nothing** — the numeric severity filter needs the exact syntax `[{"numeric": [">=", 7]}]`, array brackets included.
- **Lambda fails with AccessDenied on `iam:UpdateAccessKey`** — check the policy is attached to the Lambda's actual execution role (verify by clicking through from the Lambda's own Configuration → Permissions tab, not by searching IAM directly), and that it isn't hiding under a misleadingly generic name.
- **Testing with GuardDuty sample findings throws a "user not found" style error** — expected. Sample findings use placeholder identities that don't exist in the account. Validate remediation with a direct Lambda test event against a real decoy user instead.
- **SNS subscription confirms then dies within seconds** — check for an automatic "Unsubscribe Confirmation" email arriving right after. A mail scanner likely pre-fetched the unsubscribe link. Recreate the subscription and confirm with `--authenticate-on-unsubscribe true` via CLI to stop it happening again.

## Deep Dive

Still working through these:

1. Why does this pipeline use EventBridge instead of a CloudWatch Alarm like Project 1? What's fundamentally different between what an Alarm watches and what an EventBridge rule matches?
2. Why deactivate the one compromised access key instead of deleting the whole IAM user or attaching a deny-all policy account-wide?
3. Testing with GuardDuty's sample findings produced an error trying to deactivate a key that doesn't exist. Why, and what's the real difference between "the pipeline works" and "the remediation logic works"?
4. This rule fires the same way for a severity 7 finding and a severity 10 finding. Should it?
5. If the Lambda's IAM permission were scoped to `"Resource": "*"` instead of one specific user ARN, what's the actual blast radius if this function — or whatever triggers it — ever got manipulated by someone who shouldn't have access?

## Cleanup

1. **Delete the EventBridge rule** — `guardduty-high-severity-findings`.
2. **Delete the Lambda function** — `revoke-compromised-key`, plus its CloudWatch Logs group.
3. **Delete the Lambda's execution role and both attached policies** — including the misleadingly-named one; don't leave a real IAM-modifying grant sitting under a name that hides what it does.
4. **Delete the SNS topic** — `security-incident-alerts` — takes its subscriptions with it.
5. **Delete the EventBridge-to-SNS execution role** created separately during the build.
6. **Delete the decoy IAM user** — `lab-decoy-user`, including its access key.
7. **Disable GuardDuty** — Settings → Disable. Nothing here bills per hour, but no reason to leave it analyzing an account not in active use.

## Homework

Right now a real alert arrives as raw GuardDuty JSON — technically complete, unpleasant to read at 3 AM. Extend this with an EventBridge input transformer (or format inside the Lambda before publishing) so the page reads like something a human wrote: finding type, affected user, and the source IP/country from `service.action.awsApiCallAction.remoteIpDetails`. That single field is usually the fastest way to tell a real compromise from a false positive — your own IP showing up as the "attacker" location is a very different conversation than one from a country you've never operated in.
