import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Category, type ProductSummary } from "../api";
import { ProductCard, Section, Spinner } from "../components/Layout";
import { CategoryGlyph, ProductArt } from "../components/ProductArt";

export function Home() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [featured, setFeatured] = useState<ProductSummary[] | null>(null);
  const [deals, setDeals] = useState<ProductSummary[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => setError(true));
    api.products({ featured: true, limit: 8 }).then(setFeatured).catch(() => setError(true));
    api.products({ sort: "rating", limit: 100 })
      .then((all) => setDeals(all.filter((p) => p.compareAtPrice && p.compareAtPrice > p.price).slice(0, 4)))
      .catch(() => setError(true));
  }, []);

  const hero = featured?.[0];

  return (
    <>
      <section className="hero">
        <div className="container hero__inner">
          <div className="hero__copy">
            <span className="pill pill--glow">New season · up to 25% off</span>
            <h1>
              Electronics,<br />
              <span className="gradient-text">energized.</span>
            </h1>
            <p>Flagship phones, featherlight laptops, immersive audio and smart-home gear - curated, fast-shipped and backed by 24/7 AI support.</p>
            <div className="hero__cta">
              <Link to="/shop" className="btn btn--primary btn--lg">Shop all products</Link>
              <Link to="/shop?deals=1" className="btn btn--light btn--lg">See today's deals</Link>
            </div>
            <ul className="hero__stats">
              <li><strong>36+</strong><span>top-rated products</span></li>
              <li><strong>4.7★</strong><span>average rating</span></li>
              <li><strong>24/7</strong><span>AI assistant</span></li>
            </ul>
          </div>
          <div className="hero__visual">
            {hero ? (
              <Link to={`/product/${hero.productId}`} className="hero__product">
                <ProductArt art={hero.art} category={hero.category} size="lg" label={hero.name} />
                <div className="hero__tag">
                  <span className="eyebrow">Featured</span>
                  <strong>{hero.name}</strong>
                  <span>from ${hero.price.toLocaleString()}</span>
                </div>
              </Link>
            ) : (
              <div className="hero__placeholder" />
            )}
            <div className="orb orb--1" />
            <div className="orb orb--2" />
          </div>
        </div>
      </section>

      <section className="perks container">
        {[
          ["🚚", "Free shipping", "On orders over $50"],
          ["↩️", "30-day returns", "No questions asked"],
          ["🛡️", "Up to 3-year warranty", "On every product"],
          ["⚡", "Ask Sparky", "AI help in seconds"],
        ].map(([icon, title, sub]) => (
          <div key={title} className="perk">
            <span className="perk__icon">{icon}</span>
            <div><strong>{title}</strong><span>{sub}</span></div>
          </div>
        ))}
      </section>

      <Section title="Shop by category">
        <div className="category-grid">
          {categories.map((c) => (
            <Link key={c.id} to={`/shop?category=${c.id}`} className="category-tile">
              <CategoryGlyph category={c.id} />
              <strong>{c.name}</strong>
              <span>{c.productCount} products</span>
            </Link>
          ))}
        </div>
      </Section>

      <Section title="Trending now" action={<Link to="/shop" className="link">View all →</Link>}>
        {error && <p className="muted">The catalog is warming up. Refresh in a moment.</p>}
        {!featured && !error ? <Spinner /> : (
          <div className="product-grid">{featured?.map((p) => <ProductCard key={p.productId} p={p} />)}</div>
        )}
      </Section>

      {deals.length > 0 && (
        <Section title="Deals of the week" action={<Link to="/shop?deals=1" className="link">All deals →</Link>}>
          <div className="product-grid">{deals.map((p) => <ProductCard key={p.productId} p={p} />)}</div>
        </Section>
      )}

      <section className="container">
        <div className="banner">
          <div>
            <h2>Not sure what to buy?</h2>
            <p>Sparky, our AI assistant, compares specs, checks live stock and can even fill your cart. Look for the ⚡ button.</p>
          </div>
          <span className="banner__bolt">⚡</span>
        </div>
      </section>
    </>
  );
}
