import React, { Component } from "react";
import { NavLink, Outlet } from "react-router-dom";

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
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "#8b93a0" }}>
            {String(this.state.err?.stack || this.state.err?.message || this.state.err)}
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

export default function Layout() {
  return (
    <div style={sty.root}>
      <aside style={sty.sidebar}>
        <div style={sty.logo}>
          <div style={sty.logoIcon}>ST</div>
          <div style={sty.logoText}>SuperTrend</div>
        </div>

        <nav style={sty.nav}>
          {MENU_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              style={({ isActive }) => ({
                ...sty.navItem,
                ...(isActive ? sty.navItemActive : {}),
              })}
            >
              <span style={sty.navIcon}>{item.icon}</span>
              <span style={sty.navLabel}>{item.label}</span>
            </NavLink>
          ))}
        </nav>
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
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "0 16px 20px",
    borderBottom: "1px solid var(--border)",
    marginBottom: 12,
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
  main: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    overflow: "hidden",
  },
};
