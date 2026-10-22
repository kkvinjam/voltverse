import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Product, type ProductSummary, type Stock } from "../api";
import { useCart } from "../cart";
import { Price, ProductCard, QtyStepper, Section, Spinner, Stars } from "../components/Layout";
import { ProductArt } from "../components/ProductArt";

const STOCK_TEXT: Record<Stock["status"], string> = {
  in_stock: "In stock · ships today",
  low_stock: "Only a few left",
  out_of_stock: "Out of stock",
};

export function ProductPage() {
  const { id = "" } = useParams();
  const { add } = useCart();
  const [product, setProduct] = useState<Product | null>(null);
  const [stock, setStock] = useState<Stock | null>(null);
  const [related, setRelated] = useState<ProductSummary[]>([]);
  const [qty, setQty] = useState(1);
  const [notFound, setNotFound] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    setProduct(null);
    setNotFound(false);
    setQty(1);
    window.scrollTo({ top: 0 });
    api.product(id)
      .then((p) => {
        setProduct(p);
        api.products({ category: p.category, limit: 5 }).then((r) => setRelated(r.filter((x) => x.productId !== p.productId).slice(0, 4)));
      })
      .catch((e) => e instanceof ApiError && e.status === 404 && setNotFound(true));
    api.stock([id]).then((s) => setStock(s[0] ?? null)).catch(() => undefined);
  }, [id]);

  if (notFound) {
    return (
      <div className="container empty">
        <h2>Product not found</h2>
        <Link className="btn" to="/shop">Back to shop</Link>
      </div>
    );
  }
  if (!product) return <div className="container"><Spinner /></div>;

  const out = stock?.status === "out_of_stock";
  const maxQty = Math.min(10, stock?.available ?? 10);

  return (
    <>
      <div className="container">
        <div className="breadcrumbs">
          <Link to="/">Home</Link> / <Link to={`/shop?category=${product.category}`}>{product.category.replace("-", " ")}</Link> / <span>{product.name}</span>
        </div>
        <div className="pdp">
          <div className="pdp__media">
            <ProductArt art={product.art} category={product.category} size="lg" label={product.name} />
          </div>
          <div className="pdp__info">
            <span className="eyebrow">{product.brand}</span>
            <h1>{product.name}</h1>
            <Stars rating={product.rating} count={product.reviewCount} />
            <p className="pdp__lede">{product.shortDescription}</p>
            <Price price={product.price} compareAt={product.compareAtPrice} />
            {stock && <p className={`stock stock--${stock.status}`}>● {STOCK_TEXT[stock.status]}</p>}
            <div className="pdp__buy">
              <QtyStepper value={qty} onChange={(v) => setQty(Math.max(1, v))} max={maxQty} />
              <button
                className="btn btn--primary btn--lg"
                disabled={out || adding}
                onClick={async () => {
                  setAdding(true);
                  await add(product.productId, qty);
                  setAdding(false);
                }}
              >
                {out ? "Out of stock" : adding ? "Adding…" : "Add to cart"}
              </button>
            </div>
            <ul className="pdp__assurances">
              <li>🚚 Free standard shipping over $50</li>
              <li>↩️ 30-day returns</li>
              <li>🛡️ {product.warrantyMonths}-month warranty</li>
            </ul>
            <p>{product.description}</p>
            <h3>Specifications</h3>
            <dl className="specs">
              {Object.entries(product.specs).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
              ))}
            </dl>
          </div>
        </div>
      </div>
      {related.length > 0 && (
        <Section title="You may also like">
          <div className="product-grid">{related.map((p) => <ProductCard key={p.productId} p={p} />)}</div>
        </Section>
      )}
    </>
  );
}
