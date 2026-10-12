# Hardening and ops (run once, before judging starts)

All commands assume `AWS_PROFILE=alexa-ai-user AWS_REGION=us-east-1`.
Replace `<ACCOUNT_ID>` and `<EMAIL>` before running.

## 1. Throttle the public API (Bedrock cost guard)

**Terminal command** — look up the route keys first:

```bash
aws apigatewayv2 get-routes --api-id 7xxrr1lk9e --query 'Items[].RouteKey'
```

**Terminal command** — 5 rps for the whole API, 1 rps / burst 3 for chat
(replace `POST /chat` with the exact route key printed above):

```bash
aws apigatewayv2 update-stage --api-id 7xxrr1lk9e --stage-name '$default' \
  --default-route-settings ThrottlingBurstLimit=10,ThrottlingRateLimit=5 \
  --route-settings '{"POST /chat":{"ThrottlingBurstLimit":3,"ThrottlingRateLimit":1}}'
```

## 2. Budget alarm

**Terminal command:**

```bash
aws budgets create-budget --account-id <ACCOUNT_ID> \
  --budget '{"BudgetName":"homekeeper","BudgetLimit":{"Amount":"25","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"<EMAIL>"}]}]'
```

## 3. Demo reset (nightly + Reset button)

`store.reset_demo()` deletes the household partition and rewrites the seed, so
both roles that call it need batch write and delete.

**File content** — save as `/tmp/reset-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"],
    "Resource": "arn:aws:dynamodb:us-east-1:<ACCOUNT_ID>:table/homekeeper"
  }]
}
```

**Terminal command:**

```bash
for role in homekeeper-dailyscan-role homekeeper-chat-role; do
  aws iam put-role-policy --role-name "$role" --policy-name homekeeper-reset \
    --policy-document file:///tmp/reset-policy.json
done
aws lambda update-function-configuration --function-name homekeeper-daily-scan \
  --environment 'Variables={HOMEKEEPER_TABLE=homekeeper,DEMO_CLOCK=2026-10-04,RESEED_ON_SCAN=1}'
```

`--environment` replaces all variables, so the list above is the full set.

## 4. Keep the MCP Lambda warm

Provisioned concurrency was rejected (account concurrency limit 10). A 5-minute
ping does the same job for a demo. `lambda_handler.handler` returns early on
`{"warmup": true}`.

**Terminal command:**

```bash
aws scheduler create-schedule --name homekeeper-mcp-warm \
  --schedule-expression 'rate(5 minutes)' --flexible-time-window Mode=OFF \
  --target '{"Arn":"arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:homekeeper-mcp","RoleArn":"arn:aws:iam::<ACCOUNT_ID>:role/homekeeper-scheduler-role","Input":"{\"warmup\":true}"}'
```

The scheduler role currently allows invoking only the daily-scan function; add
`lambda:InvokeFunction` on `homekeeper-mcp` to it first. Longer term, request a
higher concurrency quota in Service Quotas and switch to provisioned concurrency.

## 5. Real demo recall

**Terminal command:**

```bash
python deploy/find_demo_recall.py --search refrigerator
python deploy/find_demo_recall.py --pick <RecallID> --brand "<Brand>" --model "<Model>"
python src/seed.py
```

Commit `src/demo_recall.json`, include it in the MCP, chat and daily-scan zips,
and redeploy all three.
