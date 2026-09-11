/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute base URL for the FastAPI backend; defaults to the `/api` proxy. */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
