import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import { useReducedMotion, useRun } from "../hooks";
import { RunDetails } from "./DetailsPage";
import { RunResult, RunStage, useRunView } from "./RunPage";
import { RestingStage, StartSection } from "./StartPage";

/** The whole app on one page, read top to bottom as the panel works: the idea and the
 * models, the panel at work, the verdict, then each critic's issues. A run has its own
 * address (/runs/<id>), which opens this page with that run. */
export function Page() {
  const { id } = useParams();
  const { run, error, missing } = useRun(id);
  const view = useRunView(run);
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLElement>(null);
  const resultRef = useRef<HTMLElement>(null);
  const behavior: ScrollBehavior = reduce ? "auto" : "smooth";

  // Opening a run, from the form or the list of past runs, brings the panel into view
  // once the run has loaded (before that the page above it is still settling).
  const scrolledFor = useRef<string | null>(null);
  const loaded = run !== null && run.id === id;
  useEffect(() => {
    if (!id || !loaded || scrolledFor.current === id) return;
    scrolledFor.current = id;
    if (window.location.hash) return;
    window.requestAnimationFrame(() => panelRef.current?.scrollIntoView({ behavior, block: "start" }));
  }, [id, loaded]); // eslint-disable-line react-hooks/exhaustive-deps

  // A run watched to its end: once the field has settled, the verdict comes into view.
  useEffect(() => {
    if (!view.showResult || !view.watched) return;
    resultRef.current?.scrollIntoView({ behavior, block: "start" });
  }, [view.showResult, view.watched]); // eslint-disable-line react-hooks/exhaustive-deps

  // The statement inks in when its section comes into view, not before.
  const [resultIn, setResultIn] = useState(false);
  useEffect(() => {
    setResultIn(false);
    const element = resultRef.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setResultIn(true);
        observer.disconnect();
      },
      { threshold: 0.15 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [id, view.showResult]);

  const replay = () => {
    view.startReplay();
    panelRef.current?.scrollIntoView({ behavior, block: "start" });
  };

  return (
    <main className="one-page">
      <StartSection currentRunId={id} />
      <section ref={panelRef} id="panel" className="stage-section" aria-label="The panel at work">
        {id ? <RunStage id={id} run={run} error={error} missing={missing} view={view} /> : <RestingStage reduce={reduce} />}
      </section>
      {id && run && view.showResult && (
        <section
          ref={resultRef}
          id="verdict"
          className={`result-section${resultIn ? " is-in" : ""}`}
          aria-label="The verdict"
        >
          <RunResult run={run} view={view} onReplay={replay} />
        </section>
      )}
      {id && run?.record && view.showResult && <RunDetails run={run} />}
    </main>
  );
}
