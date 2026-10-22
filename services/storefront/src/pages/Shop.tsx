import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Category, type ProductSummary } from "../api";
import { ProductCard, Spinner } from "../components/Layout";

const SORTS = [
  { id: "relevance", label: "Featured" },
  { id: "price_asc", label: "Price: low to high" },
  { id: "price_desc", label: "Price: high to low" },
  { id: "rating", label: "Top rated" },
];
const PRICE_BANDS = [
  { id: "", label: "Any price" },
  { id: "100", label: "Under $100" },
  { id: "500", label: "Under $500" },
  { id: "1000", label: "Under $1,000" },
];

export function Shop() {
  const [params, setParams] = useSearchParams();
  const category = params.get("category") ?? "";
  const q = params.get("q") ?? "";
  const sort = params.get("sort") ?? "relevance";
  const maxPrice = params.get("maxPrice") ?? "";
  const deals = params.get("deals") === "1";

  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<ProductSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => undefined);
  }, []);

  useEffect(() => {
    setProducts(null);
    setError(null);
    api.products({ category, q, sort, maxPrice, limit: 100 })
      .then(setProducts)
      .catch(() => setError("We couldn't load products. Please try again."));
  }, [category, q, sort, maxPrice]);

  const shown = useMemo(
    () => (deals ? products?.filter((p) => p.compareAtPrice && p.compareAtPrice > p.price) : products),
    [products, deals],
  );

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  const title = q ? `Results for “${q}”` : deals ? "Deals" : categories.find((c) => c.id === category)?.name ?? "All products";

  return (
    <div className="container shop">
      <div className="breadcrumbs"><Link to="/">Home</Link> / <span>{title}</span></div>
      <div className="shop__head">
        <h1>{title}</h1>
        <span className="muted">{shown ? `${shown.length} products` : ""}</span>
      </div>
      <div className="shop__layout">
        <aside className="filters">
          <h4>Category</h4>
          <button className={`chip ${!category ? "chip--active" : ""}`} onClick={() => set("category", "")}>All</button>
          {categories.map((c) => (
            <button key={c.id} className={`chip ${category === c.id ? "chip--active" : ""}`} onClick={() => set("category", c.id)}>
              {c.name}
            </button>
          ))}
          <h4>Price</h4>
          {PRICE_BANDS.map((b) => (
            <button key={b.id} className={`chip ${maxPrice === b.id ? "chip--active" : ""}`} onClick={() => set("maxPrice", b.id)}>
              {b.label}
            </button>
          ))}
          <h4>Offers</h4>
          <button className={`chip ${deals ? "chip--active" : ""}`} onClick={() => set("deals", deals ? "" : "1")}>On sale</button>
        </aside>
        <div>
          <div className="shop__toolbar">
            {q && <button className="chip chip--active" onClick={() => set("q", "")}>“{q}” ✕</button>}
            <label className="select">
              Sort by
              <select value={sort} onChange={(e) => set("sort", e.target.value)}>
                {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
          </div>
          {error && <p className="error">{error}</p>}
          {!shown && !error && <Spinner />}
          {shown && shown.length === 0 && (
            <div className="empty card">
              <p>No products match your filters.</p>
              <Link className="btn" to="/shop">Clear filters</Link>
            </div>
          )}
          <div className="product-grid">{shown?.map((p) => <ProductCard key={p.productId} p={p} />)}</div>
        </div>
      </div>
    </div>
  );
}
