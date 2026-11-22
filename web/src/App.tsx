import { Link, NavLink, Outlet, Route, Routes, useLocation } from "react-router";
import { useRevealGuard } from "./hooks";
import { DetailsPage } from "./pages/DetailsPage";
import { RunPage } from "./pages/RunPage";
import { StartPage } from "./pages/StartPage";

export function App() {
  return (
    <Routes>
      <Route element={<Frame />}>
        <Route index element={<StartPage />} />
        <Route path="runs/:id" element={<RunPage />} />
        <Route path="runs/:id/details" element={<DetailsPage />} />
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
      <Outlet key={location.pathname} />
    </div>
  );
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
