export type Art = { from: string; to: string };

export type ProductSummary = {
  productId: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  compareAtPrice?: number;
  rating: number;
  reviewCount: number;
  shortDescription: string;
  art: Art;
  featured?: boolean;
  tags?: string[];
};

export type Product = ProductSummary & {
  description: string;
  specs: Record<string, string>;
  warrantyMonths: number;
};

export type Category = { id: string; name: string; icon: string; productCount: number };
export type Stock = { productId: string; available: number; status: "in_stock" | "low_stock" | "out_of_stock" };

export type CartLine = {
  productId: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  art: Art;
  quantity: number;
};
export type Cart = { cartId: string; items: CartLine[]; itemCount: number; subtotal: number; currency: string };

export type Totals = { subtotal: number; shipping: number; tax: number; total: number; currency: string };
export type Quote = Totals & {
  items: CartLine[];
  shippingMethod: string;
  shippingOptions: { id: string; name: string; price: number; freeOver?: number }[];
};

export type Address = {
  name: string;
  line1: string;
  line2?: string;
  city: string;
  state: string;
  postalCode: string;
  country: string;
};

export type Order = {
  orderId: string;
  email: string;
  status: string;
  fulfilmentStatus: string;
  statusHistory: { status: string; at: number }[];
  estimatedDelivery?: number;
  cancellable: boolean;
  items: { productId: string; name: string; category: string; price: number; quantity: number; art: Art }[];
  totals: Totals;
  shippingAddress: Address;
  shippingMethod: string;
  carrier: string;
  trackingNumber: string;
  createdAt: number;
  returnRequest?: { rma: string; status: string };
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? "Please check the highlighted fields." : undefined;
    throw new ApiError(res.status, body.error ?? "error", body.message ?? detail ?? "Something went wrong");
  }
  return body as T;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  categories: () => request<{ categories: Category[] }>("/catalog/categories").then((r) => r.categories),
  products: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return request<{ products: ProductSummary[] }>(`/catalog/products?${q}`).then((r) => r.products);
  },
  product: (id: string) => request<Product>(`/catalog/products/${encodeURIComponent(id)}`),
  stock: (ids: string[]) =>
    request<{ stock: Stock[] }>(`/inventory/stock?ids=${ids.map(encodeURIComponent).join(",")}`).then((r) => r.stock),

  cart: (cartId: string) => request<Cart>(`/cart/carts/${cartId}`),
  addToCart: (cartId: string, productId: string, quantity = 1) =>
    request<Cart>(`/cart/carts/${cartId}/items`, { method: "POST", body: json({ productId, quantity }) }),
  updateCartItem: (cartId: string, productId: string, quantity: number) =>
    request<Cart>(`/cart/carts/${cartId}/items/${encodeURIComponent(productId)}`, {
      method: "PUT",
      body: json({ quantity }),
    }),

  quote: (cartId: string, shippingMethod: string) =>
    request<Quote>(`/checkout/quote?cartId=${cartId}&shippingMethod=${shippingMethod}`),
  checkout: (body: {
    cartId: string;
    email: string;
    shippingAddress: Address;
    shippingMethod: string;
    card: { number: string; expMonth: number; expYear: number; cvc: string; name: string };
  }) => request<Order>("/checkout/checkout", { method: "POST", body: json(body) }),

  order: (orderId: string, email: string) =>
    request<Order>(`/orders/orders/${encodeURIComponent(orderId)}?email=${encodeURIComponent(email)}`),
  cancelOrder: (orderId: string, email: string) =>
    request<Order>(`/orders/orders/${encodeURIComponent(orderId)}/cancel`, {
      method: "POST",
      body: json({ email, reason: "Cancelled from website" }),
    }),

  chat: (body: { messages: { role: string; content: string }[]; cartId?: string; sessionId?: string }) =>
    request<{ reply: string; sessionId: string; toolsUsed: string[]; cartChanged: boolean }>("/assistant/chat", {
      method: "POST",
      body: json(body),
    }),
};

export type ChatEvent =
  | { type: "delta"; text: string }
  | { type: "tool"; name: string }
  | { type: "done"; sessionId: string; toolsUsed: string[]; cartChanged: boolean }
  | { type: "error"; message: string };

/** Stream a Sparky reply over Server-Sent Events (POST body, so EventSource can't be used). */
export async function chatStream(
  body: { messages: { role: string; content: string }[]; cartId?: string; sessionId?: string },
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch("/api/assistant/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    let message = res.status === 429 ? "You're sending messages quickly - give Sparky a few seconds." : "Sparky is unavailable right now.";
    try {
      message = JSON.parse(text).message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, "chat_failed", message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue; // heartbeat comment
      onEvent({ type: event, ...JSON.parse(data) } as ChatEvent);
    }
  }
}

export const money = (n: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);

export const statusLabel = (s: string) =>
  s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
