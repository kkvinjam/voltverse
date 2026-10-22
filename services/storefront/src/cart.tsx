import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, type Cart } from "./api";

const CART_KEY = "voltverse.cartId";

function getCartId(): string {
  let id = localStorage.getItem(CART_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(CART_KEY, id);
  }
  return id;
}

type CartContextValue = {
  cartId: string;
  cart: Cart | null;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  refresh: () => Promise<void>;
  add: (productId: string, quantity?: number) => Promise<void>;
  update: (productId: string, quantity: number) => Promise<void>;
  reset: () => void;
  toast: string | null;
  notify: (message: string) => void;
};

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const [cartId, setCartId] = useState(getCartId);
  const [cart, setCart] = useState<Cart | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const refresh = useCallback(async () => {
    try {
      setCart(await api.cart(cartId));
    } catch {
      /* cart service unavailable - keep the last known state */
    }
  }, [cartId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const add = useCallback(
    async (productId: string, quantity = 1) => {
      try {
        setCart(await api.addToCart(cartId, productId, quantity));
        setDrawerOpen(true);
      } catch (e) {
        notify(e instanceof ApiError ? e.message : "Couldn't add to cart");
      }
    },
    [cartId, notify],
  );

  const update = useCallback(
    async (productId: string, quantity: number) => {
      try {
        setCart(await api.updateCartItem(cartId, productId, quantity));
      } catch (e) {
        notify(e instanceof ApiError ? e.message : "Couldn't update cart");
      }
    },
    [cartId, notify],
  );

  // After checkout the server clears the cart; start a fresh cart id for the next session.
  const reset = useCallback(() => {
    const id = crypto.randomUUID();
    localStorage.setItem(CART_KEY, id);
    setCartId(id);
    setCart(null);
  }, []);

  const value = useMemo(
    () => ({ cartId, cart, drawerOpen, setDrawerOpen, refresh, add, update, reset, toast, notify }),
    [cartId, cart, drawerOpen, refresh, add, update, reset, toast, notify],
  );
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart() {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used inside CartProvider");
  return ctx;
}
