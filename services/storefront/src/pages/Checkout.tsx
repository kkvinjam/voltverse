import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, money, type Quote } from "../api";
import { useCart } from "../cart";
import { QtyStepper, Spinner } from "../components/Layout";
import { ProductArt } from "../components/ProductArt";

export function CartPage() {
  const { cart, update } = useCart();
  if (!cart) return <div className="container"><Spinner /></div>;
  return (
    <div className="container narrow">
      <h1>Your cart</h1>
      {cart.items.length === 0 ? (
        <div className="empty card">
          <p>Your cart is empty.</p>
          <Link className="btn btn--primary" to="/shop">Start shopping</Link>
        </div>
      ) : (
        <div className="checkout">
          <div className="card">
            {cart.items.map((l) => (
              <div key={l.productId} className="line line--wide">
                <ProductArt art={l.art} category={l.category} size="sm" label={l.name} />
                <div className="line__info">
                  <Link to={`/product/${l.productId}`}><strong>{l.name}</strong></Link>
                  <span className="muted">{l.brand} · {money(l.price)}</span>
                </div>
                <QtyStepper value={l.quantity} onChange={(q) => update(l.productId, q)} />
                <span className="line__total">{money(l.price * l.quantity)}</span>
                <button className="btn btn--ghost" onClick={() => update(l.productId, 0)} aria-label={`Remove ${l.name}`}>Remove</button>
              </div>
            ))}
          </div>
          <aside className="card summary">
            <div className="row"><span>Subtotal ({cart.itemCount} items)</span><strong>{money(cart.subtotal)}</strong></div>
            <p className="muted small">Shipping and tax calculated at checkout.</p>
            <Link to="/checkout" className="btn btn--primary btn--block">Proceed to checkout</Link>
          </aside>
        </div>
      )}
    </div>
  );
}

const TEST_CARDS = [
  ["4242 4242 4242 4242", "Approved"],
  ["4000 0000 0000 0002", "Declined"],
  ["4000 0000 0000 9995", "Insufficient funds"],
  ["4000 0000 0000 0119", "Processor error"],
];

export function Checkout() {
  const { cartId, cart, reset } = useCart();
  const navigate = useNavigate();
  const [method, setMethod] = useState("standard");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);
  const [form, setForm] = useState({
    email: "", name: "", line1: "", line2: "", city: "", state: "", postalCode: "",
    cardName: "", cardNumber: "4242 4242 4242 4242", exp: "12/30", cvc: "123",
  });

  useEffect(() => {
    if (!cart || cart.items.length === 0) return;
    api.quote(cartId, method).then(setQuote).catch(() => setQuote(null));
  }, [cartId, cart, method]);

  const set = (k: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const [mm, yy] = form.exp.split("/").map((s) => parseInt(s.trim(), 10));
    if (!mm || !yy) return setError("Enter the card expiry as MM/YY.");
    setPlacing(true);
    try {
      const order = await api.checkout({
        cartId,
        email: form.email,
        shippingMethod: method,
        shippingAddress: { name: form.name, line1: form.line1, line2: form.line2 || undefined, city: form.city, state: form.state, postalCode: form.postalCode, country: "US" },
        card: { number: form.cardNumber, expMonth: mm, expYear: yy < 100 ? 2000 + yy : yy, cvc: form.cvc, name: form.cardName || form.name },
      });
      reset();
      navigate(`/order/${order.orderId}?email=${encodeURIComponent(order.email)}&new=1`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Checkout failed. Please try again.");
    } finally {
      setPlacing(false);
    }
  };

  if (!cart) return <div className="container"><Spinner /></div>;
  if (cart.items.length === 0) {
    return (
      <div className="container narrow empty">
        <h1>Checkout</h1>
        <p>Your cart is empty.</p>
        <Link className="btn btn--primary" to="/shop">Start shopping</Link>
      </div>
    );
  }

  return (
    <div className="container">
      <h1>Checkout</h1>
      <form className="checkout" onSubmit={submit}>
        <div className="checkout__form">
          <fieldset className="card">
            <legend>Contact</legend>
            <label>Email<input type="email" required value={form.email} onChange={set("email")} autoComplete="email" /></label>
          </fieldset>
          <fieldset className="card">
            <legend>Shipping address</legend>
            <label>Full name<input required value={form.name} onChange={set("name")} autoComplete="name" /></label>
            <label>Address<input required value={form.line1} onChange={set("line1")} autoComplete="address-line1" /></label>
            <label>Apartment, suite (optional)<input value={form.line2} onChange={set("line2")} autoComplete="address-line2" /></label>
            <div className="grid-3">
              <label>City<input required value={form.city} onChange={set("city")} autoComplete="address-level2" /></label>
              <label>State<input required value={form.state} onChange={set("state")} autoComplete="address-level1" /></label>
              <label>ZIP<input required value={form.postalCode} onChange={set("postalCode")} autoComplete="postal-code" /></label>
            </div>
          </fieldset>
          <fieldset className="card">
            <legend>Delivery</legend>
            {(quote?.shippingOptions ?? [{ id: "standard", name: "Standard (3-5 days)", price: 5.99 }, { id: "express", name: "Express (1-2 days)", price: 14.99 }]).map((o) => (
              <label key={o.id} className={`radio ${method === o.id ? "radio--active" : ""}`}>
                <input type="radio" name="ship" checked={method === o.id} onChange={() => setMethod(o.id)} />
                <span>{o.name}</span>
                <strong>{o.id === "standard" && quote && quote.subtotal >= (o.freeOver ?? Infinity) ? "Free" : money(o.price)}</strong>
              </label>
            ))}
          </fieldset>
          <fieldset className="card">
            <legend>Payment</legend>
            <div className="notice">
              <strong>Demo store - no real payments.</strong> Use a test card:
              <ul>{TEST_CARDS.map(([n, d]) => <li key={n}><button type="button" className="link" onClick={() => setForm({ ...form, cardNumber: n })}>{n}</button> {d}</li>)}</ul>
            </div>
            <label>Name on card<input value={form.cardName} onChange={set("cardName")} placeholder="Same as shipping" autoComplete="cc-name" /></label>
            <label>Card number<input required inputMode="numeric" value={form.cardNumber} onChange={set("cardNumber")} autoComplete="cc-number" /></label>
            <div className="grid-2">
              <label>Expiry (MM/YY)<input required value={form.exp} onChange={set("exp")} autoComplete="cc-exp" /></label>
              <label>CVC<input required inputMode="numeric" maxLength={4} value={form.cvc} onChange={set("cvc")} autoComplete="cc-csc" /></label>
            </div>
          </fieldset>
        </div>
        <aside className="card summary">
          <h3>Order summary</h3>
          {cart.items.map((l) => (
            <div key={l.productId} className="line">
              <ProductArt art={l.art} category={l.category} size="sm" label={l.name} />
              <div className="line__info"><strong>{l.name}</strong><span className="muted">Qty {l.quantity}</span></div>
              <span className="line__total">{money(l.price * l.quantity)}</span>
            </div>
          ))}
          <hr />
          {quote ? (
            <>
              <div className="row"><span>Subtotal</span><span>{money(quote.subtotal)}</span></div>
              <div className="row"><span>Shipping</span><span>{quote.shipping === 0 ? "Free" : money(quote.shipping)}</span></div>
              <div className="row"><span>Estimated tax</span><span>{money(quote.tax)}</span></div>
              <div className="row row--total"><span>Total</span><strong>{money(quote.total)}</strong></div>
            </>
          ) : <Spinner />}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn btn--primary btn--block btn--lg" disabled={placing}>{placing ? "Placing order…" : "Place order"}</button>
          <p className="muted small">🔒 Card data is sent over HTTPS to the payment service and never stored.</p>
        </aside>
      </form>
    </div>
  );
}
