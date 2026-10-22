import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, chatStream } from "../api";
import { useCart } from "../cart";

type Msg = { role: "user" | "assistant"; content: string; tools?: string[] };

const GREETING: Msg = {
  role: "assistant",
  content:
    "Hi, I'm **Sparky** ⚡ VoltVerse's AI assistant. I can help you find the right gadget, check stock, manage your cart, track or cancel an order and explain our policies. What can I do for you?",
};

const SUGGESTIONS = [
  "Recommend noise-cancelling headphones under $400",
  "What's the difference between the Nova X1 and X1 Pro?",
  "Add Pulse Buds Pro 2 to my cart",
  "Where is my order?",
];

const TOOL_LABELS: Record<string, string> = {
  search_products: "Searched catalog",
  get_product_details: "Read product details",
  list_categories: "Listed categories",
  check_stock: "Checked inventory",
  view_cart: "Viewed cart",
  add_to_cart: "Updated cart",
  update_cart_item: "Updated cart",
  get_checkout_quote: "Calculated totals",
  get_order_status: "Looked up order",
  cancel_order: "Cancelled order",
  request_return: "Started return",
  get_payment_status: "Checked payment",
  get_store_policies: "Read store policies",
};

// Tiny, safe markdown: **bold**, bullet lists and line breaks. No HTML is ever injected.
function renderText(text: string): ReactNode {
  const inline = (s: string, key: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={`${key}-${i}`}>{part.slice(2, -2)}</strong> : part,
    );
  const blocks: ReactNode[] = [];
  let list: ReactNode[] = [];
  text.split("\n").forEach((line, i) => {
    const m = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (m) {
      list.push(<li key={i}>{inline(m[1], `l${i}`)}</li>);
      return;
    }
    if (list.length) {
      blocks.push(<ul key={`ul${i}`}>{list}</ul>);
      list = [];
    }
    if (line.trim()) blocks.push(<p key={i}>{inline(line, `p${i}`)}</p>);
  });
  if (list.length) blocks.push(<ul key="ul-end">{list}</ul>);
  return blocks;
}

export function ChatWidget() {
  const { cartId, refresh } = useCart();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    const next: Msg[] = [...messages, { role: "user", content }];
    setMessages(next);
    setInput("");
    setBusy(true);
    // Placeholder bubble that fills in as tokens stream back.
    setMessages((m) => [...m, { role: "assistant", content: "", tools: [] }]);
    const patch = (fn: (last: Msg) => Msg) =>
      setMessages((m) => [...m.slice(0, -1), fn(m[m.length - 1])]);
    let sawTool = false;
    try {
      await chatStream(
        {
          cartId,
          sessionId,
          messages: next.filter((m) => m !== GREETING).map(({ role, content }) => ({ role, content })),
        },
        (e) => {
          if (e.type === "delta") {
            const text = sawTool ? `\n\n${e.text}` : e.text;
            sawTool = false;
            patch((last) => ({ ...last, content: last.content + text }));
          } else if (e.type === "tool") {
            sawTool = true;
            patch((last) => ({ ...last, tools: [...(last.tools ?? []), e.name] }));
          } else if (e.type === "done") {
            setSessionId(e.sessionId);
            if (e.cartChanged) refresh();
          } else if (e.type === "error") {
            patch((last) => ({ ...last, content: last.content ? `${last.content}\n\n${e.message}` : e.message }));
          }
        },
      );
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "I couldn't reach the server. Please try again.";
      patch((last) => ({ ...last, content: msg }));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <>
      <button className={`chat-fab ${open ? "chat-fab--hidden" : ""}`} onClick={() => setOpen(true)} aria-label="Chat with Sparky">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>
        <span>Ask Sparky</span>
      </button>
      <section className={`chat ${open ? "chat--open" : ""}`} aria-label="Sparky AI assistant" aria-hidden={!open}>
        <header className="chat__head">
          <div className="chat__avatar">⚡</div>
          <div>
            <strong>Sparky</strong>
            <span className="chat__status">AI shopping & support assistant</span>
          </div>
          <button className="btn btn--ghost chat__close" onClick={() => setOpen(false)} aria-label="Close chat">✕</button>
        </header>
        <div className="chat__body" ref={listRef}>
          {messages.filter((m) => m.content || m.role === "user").map((m, i) => (
            <div key={i} className={`bubble bubble--${m.role}`}>
              {renderText(m.content)}
              {m.tools && m.tools.length > 0 && (
                <div className="bubble__tools">
                  {[...new Set(m.tools.map((t) => TOOL_LABELS[t] ?? t))].map((t) => (
                    <span key={t} className="tool-chip">{t}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {busy && !messages[messages.length - 1]?.content && (
            <div className="bubble bubble--assistant bubble--typing" aria-label="Sparky is typing">
              <span /><span /><span />
            </div>
          )}
          {messages.length === 1 && (
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          )}
        </div>
        <form className="chat__input" onSubmit={submit}>
          <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about products, orders, returns…" maxLength={2000} disabled={busy} />
          <button className="btn btn--primary" disabled={busy || !input.trim()} aria-label="Send">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>
          </button>
        </form>
        <p className="chat__disclaimer">AI responses may be inaccurate. Powered by Strands Agents & Amazon Bedrock.</p>
      </section>
    </>
  );
}
