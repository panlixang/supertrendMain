import React, { Component, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useWebSocket } from "../hooks/useWebSocket";

// 路由级错误边界：任一页面渲染抛错时，只显示可读报错而非整页黑屏
class RouteErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }
  static getDerivedStateFromError(err) {
    return { err };
  }
  componentDidCatch(err, info) {
    console.error("[RouteError]", err, info);
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 40, fontFamily: "var(--font-mono)", lineHeight: 1.6 }}>
          <h2 style={{ color: "#e05263", marginBottom: 12 }}>页面渲染出错</h2>
          <div
            style={{
              padding: "12px 16px",
              marginBottom: 16,
              background: "#e0526318",
              border: "1px solid #e0526355",
              borderRadius: 8,
              color: "#ffb4bd",
              fontSize: 14,
              fontWeight: 600,
              wordBreak: "break-all",
            }}
          >
            {String(this.state.err?.message || this.state.err)}
          </div>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "#8b93a0" }}>
            {String(this.state.err?.stack || "")}
          </pre>
          <button
            onClick={() => this.setState({ err: null })}
            style={{ marginTop: 16, padding: "8px 16px", background: "#00c9a7", color: "#000", border: "none", borderRadius: 6, cursor: "pointer" }}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const MENU_ITEMS = [
  { path: "/", label: "信号终端", icon: "📊" },
  { path: "/research", label: "策略研究", icon: "🔬" },
];

const SIDEBAR_COLLAPSED_KEY = "st.sidebar.collapsed";

export default function Layout() {
  // WS 连接放在 Layout(常驻壳层):切页面不断线,研究页也能拿到实时数据
  useWebSocket();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1"
  );
  const toggle = () =>
    setCollapsed((c) => {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, c ? "0" : "1");
      return !c;
    });

  return (
    <div style={sty.root}>
      <aside style={{ ...sty.sidebar, ...(collapsed ? sty.sidebarCollapsed : {}) }}>
        <div style={{ ...sty.logo, ...(collapsed ? sty.logoCollapsed : {}) }}>
          <div style={sty.logoIcon}>ST</div>
          {!collapsed && <div style={sty.logoText}>SuperTrend</div>}
        </div>

        <nav style={{ ...sty.nav, ...(collapsed ? sty.navCollapsed : {}) }}>
          {MENU_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              title={collapsed ? item.label : undefined}
              style={({ isActive }) => ({
                ...sty.navItem,
                ...(collapsed ? sty.navItemCollapsed : {}),
                ...(isActive ? sty.navItemActive : {}),
              })}
            >
              <span style={sty.navIcon}>{item.icon}</span>
              {!collapsed && <span style={sty.navLabel}>{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        <button
          onClick={toggle}
          title={collapsed ? "展开菜单" : "收起菜单"}
          style={{ ...sty.toggle, ...(collapsed ? sty.toggleCollapsed : {}) }}
        >
          {collapsed ? "»" : "«"}
        </button>
      </aside>

      <main style={sty.main}>
        <RouteErrorBoundary>
          <Outlet />
        </RouteErrorBoundary>
      </main>
    </div>
  );
}

const sty = {
  root: {
    display: "flex",
    height: "100vh",
    overflow: "hidden",
    background: "var(--bg)",
  },
  sidebar: {
    width: 220,
    flexShrink: 0,
    background: "var(--card)",
    borderRight: "1px solid var(--border)",
    display: "flex",
    flexDirection: "column",
    padding: "16px 0",
    transition: "width .2s ease",
    overflow: "hidden",
  },
  sidebarCollapsed: {
    width: 56,
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "0 16px 20px",
    borderBottom: "1px solid var(--border)",
    marginBottom: 12,
  },
  logoCollapsed: {
    justifyContent: "center",
    padding: "0 0 20px",
  },
  logoIcon: {
    width: 32,
    height: 32,
    borderRadius: 8,
    background: "linear-gradient(135deg, #00c9a7 0%, #00a085 100%)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#fff",
    fontSize: 13,
    fontWeight: 700,
  },
  logoText: {
    fontSize: 14,
    fontWeight: 600,
    color: "#e8eaed",
  },
  nav: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "0 8px",
    flex: 1,
  },
  navCollapsed: {
    padding: "0 8px",
    alignItems: "center",
  },
  navItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "10px 12px",
    borderRadius: 8,
    textDecoration: "none",
    color: "#8b93a0",
    fontSize: 13,
    fontWeight: 500,
    transition: "all .15s",
    cursor: "pointer",
    whiteSpace: "nowrap",
  },
  navItemCollapsed: {
    justifyContent: "center",
    padding: "10px 0",
    width: 40,
  },
  navItemActive: {
    background: "#00c9a714",
    color: "#00c9a7",
  },
  navIcon: {
    fontSize: 16,
    width: 20,
    textAlign: "center",
  },
  navLabel: {
    flex: 1,
  },
  toggle: {
    margin: "0 12px",
    padding: "8px 0",
    borderRadius: 8,
    border: "1px solid var(--border)",
    background: "transparent",
    color: "#8b93a0",
    fontSize: 16,
    lineHeight: 1,
    cursor: "pointer",
    transition: "all .15s",
  },
  toggleCollapsed: {
    margin: "0 8px",
  },
  main: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    overflow: "hidden",
  },
};
