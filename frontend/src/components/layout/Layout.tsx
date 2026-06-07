import { Outlet } from "react-router-dom";
import { useEffect } from "react";
import Navbar from "./Navbar";
import { useAppStore } from "../../stores/appStore";

export default function Layout() {
  const token = useAppStore((s) => s.token);
  const setAuth = useAppStore((s) => s.setAuth);

  useEffect(() => {
    const stored = localStorage.getItem("asr-auth");
    if (stored && !token) {
      try {
        const { user, token: t } = JSON.parse(stored);
        setAuth(user, t);
      } catch {}
    }
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <Navbar />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
