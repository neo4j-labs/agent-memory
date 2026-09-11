import { ChakraProvider } from '@chakra-ui/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { labsSystem } from './theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
})

const container = document.getElementById('root')
if (!container) throw new Error('Root element #root not found in index.html')

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ChakraProvider value={labsSystem}>
        <App />
      </ChakraProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
