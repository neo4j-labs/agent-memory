import { Box, Flex, Spinner } from '@chakra-ui/react'
import { Suspense, lazy } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router'
import ChatInterface from './components/Chat/ChatInterface'
import AlertsPanel from './components/Dashboard/AlertsPanel'
import CustomerDashboard from './components/Dashboard/CustomerDashboard'
import Sidebar from './components/Dashboard/Sidebar'
import InvestigationPanel from './components/Investigation/InvestigationPanel'

// NVL is the heaviest dependency in the app; load it only on the graph route.
const MemoryGraphView = lazy(() => import('./components/Graph/MemoryGraphView'))

function RouteFallback() {
  return (
    <Flex h="calc(100dvh - 48px)" align="center" justify="center">
      <Spinner size="lg" colorPalette="brand" />
    </Flex>
  )
}

function App() {
  return (
    <Router>
      <Flex minH="100dvh" bg="bg">
        <Sidebar />
        <Box flex="1" bg="bg.subtle" p={6} minW={0}>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/" element={<CustomerDashboard />} />
              <Route path="/chat" element={<ChatInterface />} />
              <Route path="/customers" element={<CustomerDashboard />} />
              <Route path="/investigations" element={<InvestigationPanel />} />
              <Route path="/alerts" element={<AlertsPanel />} />
              <Route path="/graph" element={<MemoryGraphView />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </Box>
      </Flex>
    </Router>
  )
}

export default App
