import type { ReactElement, ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../components/ui/Toast";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  route?: string;
  /** Route pattern used for the rendered element, so useParams works. */
  path?: string;
  queryClient?: QueryClient;
}

/** Renders UI inside a fresh QueryClient, a MemoryRouter and the toast host. */
export function renderWithProviders(ui: ReactElement, options: RenderWithProvidersOptions = {}) {
  const { route = "/", path = "*", queryClient = createTestQueryClient(), ...renderOptions } = options;
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <ToastProvider>
            <Routes>
              <Route path={path} element={children} />
              <Route path="*" element={children} />
            </Routes>
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );
  }
  return { queryClient, ...render(ui, { wrapper: Wrapper, ...renderOptions }) };
}
