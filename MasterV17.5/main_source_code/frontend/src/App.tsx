import { Routes, Route } from "react-router-dom";
import Home from "@/pages/Home";
import Trade from "@/pages/Trade";
import AutoTrade from "@/pages/AutoTrade";
import Workspace from "@/pages/Workspace";
import FeedHealthBanner from "@/components/terminal/FeedHealthBanner";
import ApiKeyGate from "@/components/terminal/ApiKeyGate";
import NetworkLagOverlay from "@/components/terminal/NetworkLagOverlay";
import BootSplash from "@/components/terminal/BootSplash";
import "@/components/terminal/terminal-additions.css";
import "@/components/terminal/v163.css";

// One <Route> per page in src/pages; BrowserRouter already wraps this in main.tsx.
export default function App() {
  return (
    <ApiKeyGate>
      <BootSplash />
      <FeedHealthBanner />
      <NetworkLagOverlay />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/trade" element={<Trade />} />
        <Route path="/autotrade" element={<AutoTrade />} />
        {['signals', 'analytics', 'flow', 'logs', 'settings', 'training', 'accuracy', 'sniffer'].map(path => <Route key={path} path={`/${path}`} element={<Workspace />} />)}
        <Route path="*" element={<Home />} />
      </Routes>
    </ApiKeyGate>
  );
}
