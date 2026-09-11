// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ChakraProvider } from "@chakra-ui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { neo4jLabsSystem } from "./theme";

/**
 * A mount smoke test: it catches the class of breakage that a type-check cannot
 * — a missing Chakra sub-component, a router API that moved, a provider that is
 * not wrapped — and asserts that every route in `App` is reachable from the
 * sidebar (the Context Graph view was routed but unlinked for a while).
 */
function renderApp() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })),
    ),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChakraProvider value={neo4jLabsSystem}>
        <App />
      </ChakraProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("mounts and renders the sidebar", () => {
    renderApp();
    expect(screen.getByText("Financial Advisor")).toBeTruthy();
  });

  it("links every route to a sidebar entry", () => {
    renderApp();
    const hrefs = new Set(
      Array.from(document.querySelectorAll("a[href]")).map((a) =>
        a.getAttribute("href"),
      ),
    );
    for (const route of [
      "/",
      "/chat",
      "/customers",
      "/investigations",
      "/alerts",
      "/graph",
    ]) {
      expect(hrefs.has(route), `no sidebar link for ${route}`).toBe(true);
    }
  });
});
