import { Suspense, lazy, useCallback, useMemo, useState } from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router";
import { Box, Flex, Spinner } from "@chakra-ui/react";
import Sidebar from "./components/Dashboard/Sidebar";
import CustomerDashboard from "./components/Dashboard/CustomerDashboard";
import ChatInterface from "./components/Chat/ChatInterface";
import InvestigationPanel from "./components/Investigation/InvestigationPanel";
import AlertsPanel from "./components/Dashboard/AlertsPanel";
import {
  ChatSessionContext,
  loadStoredSessionId,
  storeSessionId,
} from "./lib/session";

// NVL is by far the heaviest dependency here, and only one route needs it, so
// the Context Graph view is loaded on demand.
const MemoryGraphView = lazy(() => import("./components/Graph/MemoryGraphView"));

function App() {
  // The chat session id is app-level state: the Context Graph route uses it to
  // scope the graph to the current conversation, and it survives a reload so
  // the reasoning traces stored in Neo4j can be replayed.
  const [sessionId, setSessionIdState] = useState<string | null>(() =>
    loadStoredSessionId(),
  );

  const setSessionId = useCallback((next: string | null) => {
    setSessionIdState(next);
    storeSessionId(next);
  }, []);

  const sessionValue = useMemo(
    () => ({ sessionId, setSessionId }),
    [sessionId, setSessionId],
  );

  return (
    <ChatSessionContext value={sessionValue}>
      <Router>
        <Flex minH="100vh">
          <Sidebar />
          <Box flex="1" bg="bg.subtle" p={6} overflowY="auto">
            <Routes>
              <Route path="/" element={<CustomerDashboard />} />
              <Route path="/chat" element={<ChatInterface />} />
              <Route path="/customers" element={<CustomerDashboard />} />
              <Route path="/investigations" element={<InvestigationPanel />} />
              <Route path="/alerts" element={<AlertsPanel />} />
              <Route
                path="/graph"
                element={
                  <Suspense
                    fallback={
                      <Flex h="50vh" align="center" justify="center">
                        <Spinner size="lg" />
                      </Flex>
                    }
                  >
                    <MemoryGraphView />
                  </Suspense>
                }
              />
            </Routes>
          </Box>
        </Flex>
      </Router>
    </ChatSessionContext>
  );
}

export default App;
