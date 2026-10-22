import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { money, type ProductSummary } from "../api";
import { useCart } from "../cart";
import { ProductArt } from "./ProductArt";

export function Logo() {
  return (
    <Link to="/" className="logo" aria-label="VoltVerse home">
      <svg viewBox="0 0 64 64" width="34" height="34" aria-hidden="true">
        <defs>
          <linearGradient id="logo-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#6C5CE7" />
            <stop offset="1" stopColor="#00D1FF" />
          </linearGradient>
        </defs>
        <rect width="64" height="64" rx="16" fill="url(#logo-g)" />
        <path d="M36 8 18 36h12l-4 20 20-30H34z" fill="#fff" />
      </svg>
      <span>
        Volt<b>Verse</b>
      </span>
    </Link>
  );
}

const NAV = [
  { to: "/shop?category=smartphones", label: "Phones" },
  { to: "/shop?category=laptops", label: "Laptops" },
  { to: "/shop?category=audio", label: "Audio" },
  { to: "/shop?category=gaming", label: "Gaming" },
  { to: "/shop?category=smart-home", label: "Smart Home" },
  { to: "/shop?deals=1", label: "Deals" },
];

export function Header() {
  const { cart, setDrawerOpen } = useCart();
  const [params] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => setQ(params.get("q") ?? ""), [params]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    navigate(q.trim() ? `/shop?q=${encodeURIComponent(q.trim())}` : "/shop");
  };

  return (
    <header className="header">
      <div className="announcement">Free shipping on orders over $50 · 30-day returns · Ask Sparky, our AI assistant, anything</div>
      <div className="header__main container">
        <Logo />
        <form className="search" onSubmit={submit} role="search">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search phones, headphones, drones…" aria-label="Search products" />
        </form>
        <nav className="header__actions">
          <NavLink to="/track" className="icon-link">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h11v9H3zM14 10h4l3 3v3h-7" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></svg>
            <span>Track order</span>
          </NavLink>
          <button className="cart-button" onClick={() => setDrawerOpen(true)} aria-label="Open cart">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h2l2.4 11.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6" /><circle cx="10" cy="20" r="1.4" /><circle cx="17" cy="20" r="1.4" /></svg>
            {cart && cart.itemCount > 0 && <span className="badge">{cart.itemCount}</span>}
          </button>
        </nav>
      </div>
      <nav className="subnav">
        <div className="container subnav__inner">
          {NAV.map((n) => (
            <Link key={n.to} to={n.to} className={location.pathname + location.search === n.to ? "active" : ""}>
              {n.label}
            </Link>
          ))}
          <Link to="/shop">All products</Link>
        </div>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="container footer__grid">
        <div>
          <Logo />
          <p className="muted">Electronics, energized. A demo store for learning microservices, AI agents and OpenTelemetry on Amazon EKS.</p>
        </div>
        <div>
          <h4>Shop</h4>
          <Link to="/shop?category=smartphones">Smartphones</Link>
          <Link to="/shop?category=laptops">Laptops & Tablets</Link>
          <Link to="/shop?category=wearables">Wearables</Link>
          <Link to="/shop?category=drones">Drones</Link>
        </div>
        <div>
          <h4>Help</h4>
          <Link to="/track">Track an order</Link>
          <span>Shipping & returns</span>
          <span>Warranty</span>
          <span>support@voltverse.example</span>
        </div>
        <div>
          <h4>Why VoltVerse</h4>
          <span>Free shipping over $50</span>
          <span>30-day hassle-free returns</span>
          <span>24/7 AI-powered support</span>
        </div>
      </div>
      <div className="container footer__legal muted">© {new Date().getFullYear()} VoltVerse (demo). No real orders are fulfilled and no real payments are taken.</div>
    </footer>
  );
}

export function Stars({ rating, count }: { rating: number; count?: number }) {
  return (
    <span className="stars" aria-label={`Rated ${rating} out of 5`}>
      <span className="stars__track">
        ★★★★★<span className="stars__fill" style={{ width: `${(rating / 5) * 100}%` }}>★★★★★</span>
      </span>
      <span className="stars__label">{rating.toFixed(1)}{count !== undefined && ` (${count.toLocaleString()})`}</span>
    </span>
  );
}

export function Price({ price, compareAt }: { price: number; compareAt?: number }) {
  return (
    <span className="price">
      <span className="price__now">{money(price)}</span>
      {compareAt && compareAt > price && (
        <>
          <span className="price__was">{money(compareAt)}</span>
          <span className="price__save">Save {money(compareAt - price)}</span>
        </>
      )}
    </span>
  );
}

export function ProductCard({ p }: { p: ProductSummary }) {
  const { add } = useCart();
  return (
    <article className="card product-card">
      <Link to={`/product/${p.productId}`} className="product-card__media">
        <ProductArt art={p.art} category={p.category} label={p.name} />
        {p.compareAtPrice && p.compareAtPrice > p.price && <span className="pill pill--deal">Deal</span>}
      </Link>
      <div className="product-card__body">
        <span className="eyebrow">{p.brand}</span>
        <Link to={`/product/${p.productId}`} className="product-card__title">{p.name}</Link>
        <p className="product-card__desc">{p.shortDescription}</p>
        <Stars rating={p.rating} count={p.reviewCount} />
        <div className="product-card__footer">
          <Price price={p.price} compareAt={p.compareAtPrice} />
          <button className="btn btn--icon" onClick={() => add(p.productId)} aria-label={`Add ${p.name} to cart`}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </button>
        </div>
      </div>
    </article>
  );
}

export function CartDrawer() {
  const { cart, drawerOpen, setDrawerOpen, update } = useCart();
  const navigate = useNavigate();
  const go = (path: string) => {
    setDrawerOpen(false);
    navigate(path);
  };
  return (
    <>
      <div className={`scrim ${drawerOpen ? "scrim--open" : ""}`} onClick={() => setDrawerOpen(false)} />
      <aside className={`drawer ${drawerOpen ? "drawer--open" : ""}`} aria-hidden={!drawerOpen} aria-label="Shopping cart">
        <div className="drawer__head">
          <h3>Your cart</h3>
          <button className="btn btn--ghost" onClick={() => setDrawerOpen(false)} aria-label="Close cart">✕</button>
        </div>
        {!cart || cart.items.length === 0 ? (
          <div className="empty">
            <p>Your cart is empty.</p>
            <button className="btn" onClick={() => go("/shop")}>Start shopping</button>
          </div>
        ) : (
          <>
            <ul className="drawer__items">
              {cart.items.map((l) => (
                <li key={l.productId} className="line">
                  <ProductArt art={l.art} category={l.category} size="sm" label={l.name} />
                  <div className="line__info">
                    <strong>{l.name}</strong>
                    <span className="muted">{money(l.price)}</span>
                    <QtyStepper value={l.quantity} onChange={(q) => update(l.productId, q)} />
                  </div>
                  <span className="line__total">{money(l.price * l.quantity)}</span>
                </li>
              ))}
            </ul>
            <div className="drawer__foot">
              <div className="row"><span>Subtotal</span><strong>{money(cart.subtotal)}</strong></div>
              <p className="muted small">
                {cart.subtotal >= 50 ? "You've unlocked free standard shipping." : `Add ${money(50 - cart.subtotal)} more for free shipping.`}
              </p>
              <button className="btn btn--primary btn--block" onClick={() => go("/checkout")}>Checkout</button>
              <button className="btn btn--ghost btn--block" onClick={() => go("/cart")}>View cart</button>
            </div>
          </>
        )}
      </aside>
    </>
  );
}

export function QtyStepper({ value, onChange, max = 10 }: { value: number; onChange: (v: number) => void; max?: number }) {
  return (
    <span className="qty">
      <button onClick={() => onChange(value - 1)} aria-label="Decrease quantity">−</button>
      <span>{value}</span>
      <button onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max} aria-label="Increase quantity">+</button>
    </span>
  );
}

export function Toast() {
  const { toast } = useCart();
  return <div className={`toast ${toast ? "toast--show" : ""}`} role="status">{toast}</div>;
}

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="section container">
      <div className="section__head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Spinner() {
  return <div className="spinner" aria-label="Loading" />;
}
