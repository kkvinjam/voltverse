import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { CartProvider } from "./cart";
import { ChatWidget } from "./components/ChatWidget";
import { CartDrawer, Footer, Header, Toast } from "./components/Layout";
import { CartPage, Checkout } from "./pages/Checkout";
import { Home } from "./pages/Home";
import { OrderPage, TrackOrder } from "./pages/Orders";
import { ProductPage } from "./pages/ProductPage";
import { Shop } from "./pages/Shop";
import "./styles.css";

function NotFound() {
  return (
    <div className="container narrow empty">
      <h1>Page not found</h1>
      <Link className="btn btn--primary" to="/">Go home</Link>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <CartProvider>
        <Header />
        <main>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/shop" element={<Shop />} />
            <Route path="/product/:id" element={<ProductPage />} />
            <Route path="/cart" element={<CartPage />} />
            <Route path="/checkout" element={<Checkout />} />
            <Route path="/order/:id" element={<OrderPage />} />
            <Route path="/track" element={<TrackOrder />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
        <Footer />
        <CartDrawer />
        <ChatWidget />
        <Toast />
      </CartProvider>
    </BrowserRouter>
  </StrictMode>,
);
