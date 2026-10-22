# VoltVerse workshop guide

Suggested hands-on labs, about 2 hours in total. Each lab works with the stack defaults.

## Before the event

1. Launch one stack per participant account (see the README). The `loadgen` deployment starts producing traffic immediately.
2. In each account, confirm that Bedrock is enabled for the Anthropic model. Then send one message to Sparky.
3. Optional:
   - give participants `kubectl`: set `ParticipantRoleArn`, and either `EnablePublicClusterEndpoint=true` with `PublicEndpointCidrs` set to your network, or a CloudShell VPC environment
   - restrict the store with `AllowedViewerCidrs`

## Lab 1 — Shop and meet Sparky (15 min)

1. Open `StorefrontUrl`. Browse a category, search for "noise cancelling", and open a product.
2. Ask Sparky: *"I commute by train and want headphones under $400 - what do you recommend?"* Watch the tool chips (Searched catalog, Checked inventory…) appear as it works.
3. Ask Sparky to add a product to your cart. The cart badge updates.
4. Check out with `4242 4242 4242 4242`. Note the order number.
5. Ask Sparky: *"Where is my order VV-…? My email is …"*. Then: *"Please cancel it."* Sparky asks you to confirm first.

## Lab 2 — Follow a request across services (20 min)

1. Open **CloudWatch → Trace Map** (`TraceMapUrl`) and find `storefront → checkout → {cart, catalog, inventory, payment, orders}` and `→ DynamoDB`.
2. Search traces by annotation: `annotation.voltverse_order_id = "VV-…"`, using your order number. X-Ray converts dots in annotation names to underscores.
3. Open the trace and expand it:
   - `checkout.reserve_inventory` contains a DynamoDB `TransactWriteItems` call
   - `checkout.authorize_payment` includes the simulated processor latency
4. Copy the trace id and run this in **Logs Insights** against `ApplicationLogGroup`:
   ```
   fields @timestamp, resource.service.name, body
   | filter trace_id = "<trace id without the 1- prefix and dashes>"
   | sort @timestamp asc
   ```

## Lab 3 — Observe the AI agent (20 min)

1. Have a multi-step conversation with Sparky that compares products and checks the cart.
2. Find the `assistant` trace for it. Agent spans are nested:
   - `invoke_agent sparky`
   - → `execute_event_loop_cycle`
   - → `chat` (the Bedrock call, with `gen_ai.usage.*` token attributes)
   - → `execute_tool search_products`
   - → HTTP client span → `catalog` server span
3. On the dashboard, look at **Sparky: tool calls** and **end-to-end latency**. In CloudWatch Metrics, browse `VoltVerse` → `gen_ai.client.token.usage` for token consumption.
4. Discussion: which tools dominate latency, and how would you cache them?

## Lab 4 — Break things and find out (30 min)

The facilitator (or participants with kubectl) injects faults:

```bash
# 30% payment processor failures + 1.5s latency
kubectl -n voltverse patch configmap voltverse-chaos -p '{"data":{"PAYMENT_FAILURE_RATE":"0.3","PAYMENT_LATENCY_MS":"1500"}}'
kubectl -n voltverse rollout restart deploy/payment
```

Participants then use the dashboard (checkout outcomes, payment authorizations), the trace map (error and latency on the `payment` node) and logs (`payment processor unavailable`) to find the root cause. Then reset:

```bash
kubectl -n voltverse patch configmap voltverse-chaos -p '{"data":{"PAYMENT_FAILURE_RATE":"0","PAYMENT_LATENCY_MS":"0"}}'
kubectl -n voltverse rollout restart deploy/payment
```

Other scenarios:
- `CHAOS_LATENCY_MS=400` slows every service. Find which service it hurts most.
- `CHAOS_ERROR_RATE=0.1`, restarting only `inventory`. Watch the checkout `out_of_stock` / error outcomes and the cart errors.
- Scale `catalog` to 0 replicas and ask Sparky for recommendations. It should explain that it can't reach the catalog.
- Pay with card `4000 0000 0000 0119` for a deterministic processor error.

## Lab 5 — Bring your own backend (15 min)

OpenTelemetry is vendor-neutral.
1. Update the stack with `ExternalOtlpEndpoint=https://<your-otlp-endpoint>`, or edit `k8s/10-otel-collector.yaml` to add headers or authentication.
2. Redeploy.
3. The same traces, metrics and logs now also arrive in the second backend, with no application changes.

## Cleanup

Delete the stack. Everything the stack created is removed, including images, tables and log groups.
