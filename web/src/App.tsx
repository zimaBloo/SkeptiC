import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useParams } from "react-router";
import { useRevealGuard } from "./hooks";
import { Page } from "./pages/Page";

export function App() {
  return (
    <Routes>
      <Route element={<Frame />}>
        <Route index element={<Page />} />
        <Route path="runs/:id" element={<Page />} />
        {/* The details used to be a page of their own; they are a section now. */}
        <Route path="runs/:id/details" element={<DetailsRedirect />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function Frame() {
  const location = useLocation();
  return (
    <div className="frame" ref={useRevealGuard<HTMLDivElement>(location.pathname)}>
      <header className="topline">
        <Link to="/" className="wordmark" aria-label="SkeptiC, start a new run">
          Skepti<span>C</span>
        </Link>
        <nav aria-label="Main">
          <NavLink to="/" end>
            New run
          </NavLink>
        </nav>
      </header>
      <Outlet />
    </div>
  );
}

function DetailsRedirect() {
  const { id } = useParams();
  const { hash } = useLocation();
  return <Navigate to={`/runs/${id ?? ""}${hash || "#issues"}`} replace />;
}

function NotFound() {
  return (
    <main className="start page-enter">
      <section className="start-intro">
        <h1>Nothing here.</h1>
        <p className="start-lede">
          <Link to="/">Start a new run</Link>
        </p>
      </section>
    </main>
  );
}
