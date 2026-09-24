import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import AuthGuard from "./components/AuthGuard";
import { AuthProvider } from "./lib/AuthContext";
import AboutPage from "./pages/AboutPage";
import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import LoginPage from "./pages/LoginPage";

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/chat"
          element={
            <AuthGuard>
              <AppShell>
                <ChatPage />
              </AppShell>
            </AuthGuard>
          }
        />
        <Route
          path="/documents"
          element={
            <AuthGuard>
              <AppShell>
                <DocumentsPage />
              </AppShell>
            </AuthGuard>
          }
        />
        <Route
          path="/about"
          element={
            <AuthGuard>
              <AppShell>
                <AboutPage />
              </AppShell>
            </AuthGuard>
          }
        />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
