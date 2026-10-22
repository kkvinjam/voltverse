import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, money, statusLabel, type Order } from "../api";
import { Spinner } from "../components/Layout";
import { ProductArt } from "../components/ProductArt";

const STAGES = ["CONFIRMED", "PROCESSING", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"];
const fmt = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export function TrackOrder() {
  const navigate = useNavigate();
  const [orderId, setOrderId] = useState("");
  const [email, setEmail] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    navigate(`/order/${encodeURIComponent(orderId.trim().toUpperCase())}?email=${encodeURIComponent(email.trim())}`);
  };
  return (
    <div className="container narrow">
      <h1>Track your order</h1>
      <p className="muted">Enter the order number from your confirmation and the email you used at checkout.</p>
      <form className="card form-stack" onSubmit={submit}>
        <label>Order number<input required placeholder="VV-XXXXXXXX" value={orderId} onChange={(e) => setOrderId(e.target.value)} /></label>
        <label>Email<input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <button className="btn btn--primary">Find order</button>
      </form>
      <p className="muted small">Tip: you can also ask Sparky “where is my order?”</p>
    </div>
  );
}

export function OrderPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const email = params.get("email") ?? "";
  const isNew = params.get("new") === "1";
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    if (!email) return setError("Add the email used at checkout to view this order.");
    api.order(id, email).then(setOrder).catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load order"));
  }, [id, email]);

  if (error) {
    return (
      <div className="container narrow empty">
        <h2>We couldn't find that order</h2>
        <p className="muted">{error}</p>
        <Link className="btn" to="/track">Try again</Link>
      </div>
    );
  }
  if (!order) return <div className="container"><Spinner /></div>;

  const current = STAGES.indexOf(order.fulfilmentStatus);
  const cancel = async () => {
    if (!window.confirm(`Cancel order ${order.orderId}? You'll be refunded ${money(order.totals.total)}.`)) return;
    setCancelling(true);
    try {
      setOrder(await api.cancelOrder(order.orderId, email));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't cancel the order");
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="container narrow">
      {isNew && (
        <div className="success card">
          <span className="success__icon">✓</span>
          <div>
            <h2>Thanks, your order is confirmed!</h2>
            <p>We've emailed a receipt to <strong>{order.email}</strong> (not really - this is a demo).</p>
          </div>
        </div>
      )}
      <div className="order-head">
        <div>
          <span className="eyebrow">Order</span>
          <h1>{order.orderId}</h1>
          <span className="muted">Placed {fmt(order.createdAt)}</span>
        </div>
        <span className={`status status--${order.fulfilmentStatus.toLowerCase()}`}>{statusLabel(order.fulfilmentStatus)}</span>
      </div>

      {order.fulfilmentStatus !== "CANCELLED" && (
        <div className="card">
          <ol className="timeline">
            {STAGES.map((s, i) => (
              <li key={s} className={i <= current ? "done" : ""}>
                <span className="dot" />
                <span>{statusLabel(s)}</span>
              </li>
            ))}
          </ol>
          <div className="order-meta">
            <div><span className="muted">Carrier</span><strong>{order.carrier}</strong></div>
            <div><span className="muted">Tracking</span><strong>{order.trackingNumber}</strong></div>
            {order.estimatedDelivery && <div><span className="muted">Estimated delivery</span><strong>{fmt(order.estimatedDelivery)}</strong></div>}
          </div>
        </div>
      )}

      <div className="checkout">
        <div className="card">
          <h3>Items</h3>
          {order.items.map((l) => (
            <div key={l.productId} className="line">
              <ProductArt art={l.art} category={l.category} size="sm" label={l.name} />
              <div className="line__info"><Link to={`/product/${l.productId}`}><strong>{l.name}</strong></Link><span className="muted">Qty {l.quantity}</span></div>
              <span className="line__total">{money(l.price * l.quantity)}</span>
            </div>
          ))}
        </div>
        <aside className="card summary">
          <div className="row"><span>Subtotal</span><span>{money(order.totals.subtotal)}</span></div>
          <div className="row"><span>Shipping</span><span>{order.totals.shipping === 0 ? "Free" : money(order.totals.shipping)}</span></div>
          <div className="row"><span>Tax</span><span>{money(order.totals.tax)}</span></div>
          <div className="row row--total"><span>Total</span><strong>{money(order.totals.total)}</strong></div>
          <hr />
          <p className="small"><strong>Ship to</strong><br />{order.shippingAddress.name}<br />{order.shippingAddress.line1}<br />{order.shippingAddress.city}, {order.shippingAddress.state} {order.shippingAddress.postalCode}</p>
          {order.cancellable && (
            <button className="btn btn--danger btn--block" onClick={cancel} disabled={cancelling}>{cancelling ? "Cancelling…" : "Cancel order"}</button>
          )}
          {order.status === "CANCELLED" && <p className="muted small">This order was cancelled and refunded.</p>}
        </aside>
      </div>
    </div>
  );
}
