import type { Metadata } from "next";
import Link from "next/link";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Sift — AI job search that tailors your resume to every role",
  description:
    "Sift scans every major job board, scores your fit honestly, and builds a tailored resume package for each role — using only the experience you actually have. Preview your matches before you pay a cent.",
};

// ─── Reusable layout helpers ────────────────────────────────────────────────

function Container({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-6xl px-4">{children}</div>;
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
      {children}
    </p>
  );
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      {/* ── Nav ──────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b border-border bg-background/90 backdrop-blur-sm">
        <Container>
          <nav
            aria-label="Main navigation"
            className="flex h-14 items-center justify-between"
          >
            {/* Logo */}
            <Link
              href="/"
              className="flex items-center gap-2 font-semibold text-foreground"
            >
              <span
                aria-hidden="true"
                className="flex h-6 w-6 items-center justify-center rounded bg-primary text-xs font-bold text-primary-foreground"
              >
                S
              </span>
              <span>Sift</span>
            </Link>

            {/* Right-side actions */}
            <div className="flex items-center gap-2">
              <Link
                href="/login"
                className="hidden sm:inline-flex items-center rounded-lg px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                Sign in
              </Link>
              <Link href="/login">
                <Button size="sm">Start free</Button>
              </Link>
            </div>
          </nav>
        </Container>
      </header>

      <main>
        {/* ── Hero ─────────────────────────────────────────────────────── */}
        <section className="py-20 text-center md:py-28">
          <Container>
            <div className="mx-auto max-w-3xl">
              <Eyebrow>AI Job Search</Eyebrow>
              <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight text-foreground md:text-5xl lg:text-6xl">
                See every job worth applying to — then apply with a resume built
                for each one.
              </h1>
              <p className="mt-6 text-lg text-muted-foreground">
                Sift scans dozens of boards simultaneously, scores your fit
                using only the experience you actually have, and generates a
                tailored resume package for every shortlisted role — honestly,
                with no invented credentials.
              </p>

              {/* CTAs */}
              <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <Link href="/login">
                  <Button size="lg">Create your master resume</Button>
                </Link>
                <a href="#how-it-works">
                  <Button variant="outline" size="lg">
                    See how it works
                  </Button>
                </a>
              </div>

              {/* Demo metric panel */}
              <div className="mt-12 rounded-lg border border-border p-6">
                <Eyebrow>
                  Last run · Frontend Engineer, Remote EU · sample
                </Eyebrow>
                <div className="mt-4 grid grid-cols-2 gap-6 md:grid-cols-4">
                  {[
                    { label: "Vacancies scanned", value: "≈1,080" },
                    { label: "Processed", value: "200" },
                    { label: "Tailored", value: "200" },
                    { label: "Sources", value: "6" },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex flex-col gap-1">
                      <span className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                        {label}
                      </span>
                      <span className="font-mono text-3xl font-light text-foreground">
                        {value}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Container>
        </section>

        {/* ── Problem / Solution ───────────────────────────────────────── */}
        <section className="py-16 md:py-20">
          <Container>
            <div className="mb-10 text-center">
              <Eyebrow>The Problem</Eyebrow>
              <h2 className="mt-3 text-3xl font-bold text-foreground md:text-4xl">
                Job searching is still manual and scattered.
              </h2>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {/* The manual way */}
              <div className="rounded-lg border border-border bg-card p-6">
                <Eyebrow>The Manual Way</Eyebrow>
                <ul className="mt-4 space-y-3 text-muted-foreground">
                  <li>Checking five boards every morning for new postings</li>
                  <li>Sending the same generic resume to every application</li>
                  <li>Losing track of where you applied and what happened</li>
                  <li>
                    Guessing which roles are worth the effort — with no score
                  </li>
                </ul>
              </div>

              {/* With Sift */}
              <div className="rounded-lg border border-border bg-card p-6">
                <Eyebrow>With Sift</Eyebrow>
                <ul className="mt-4 space-y-3 text-muted-foreground">
                  <li>One master CV as your source of truth — entered once</li>
                  <li>
                    Set your criteria once; Sift scans all major sources
                    automatically
                  </li>
                  <li>Every shortlisted role gets a tailored resume package</li>
                  <li>
                    A full match count so you know the size of your market
                  </li>
                </ul>
              </div>
            </div>
          </Container>
        </section>

        {/* ── How it works ─────────────────────────────────────────────── */}
        <section id="how-it-works" className="py-16 md:py-20">
          <Container>
            <div className="mb-10 text-center">
              <Eyebrow>How It Works</Eyebrow>
              <h2 className="mt-3 text-3xl font-bold text-foreground md:text-4xl">
                Set it up once. Run it whenever.
              </h2>
            </div>
            <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 md:grid-cols-4">
              {[
                {
                  num: "01",
                  title: "Build your master resume",
                  desc: "Enter your real experience, skills, and education once. Sift never adds anything you haven't listed.",
                },
                {
                  num: "02",
                  title: "Set your criteria",
                  desc: "Define role keywords, locations, work format, and how many results to process per run.",
                },
                {
                  num: "03",
                  title: "Run the search",
                  desc: "Sift hits every major job board, deduplicates results, and scores each role against your master CV.",
                },
                {
                  num: "04",
                  title: "Review & apply",
                  desc: "Inspect each tailored package — resume, cover letter, ATS keywords — then apply with confidence.",
                },
              ].map(({ num, title, desc }) => (
                <div key={num} className="flex flex-col gap-3">
                  <span className="font-mono text-2xl font-medium text-primary">
                    {num}
                  </span>
                  <h3 className="font-semibold text-foreground">{title}</h3>
                  <p className="text-sm text-muted-foreground">{desc}</p>
                </div>
              ))}
            </div>
          </Container>
        </section>

        {/* ── Trust block ──────────────────────────────────────────────── */}
        <section className="py-16 md:py-20">
          <Container>
            <div className="mx-auto max-w-2xl">
              <div className="border-l-2 border-primary/60 pl-6">
                <Eyebrow>Why Teams Trust It</Eyebrow>
                <h2 className="mt-3 text-3xl font-bold text-foreground md:text-4xl">
                  Sift never invents experience you don&apos;t have.
                </h2>
                <p className="mt-4 text-muted-foreground">
                  Every tailored resume is a reordering and refocusing of what
                  you&apos;ve already written — not a fabrication. Skills,
                  titles, and years that don&apos;t appear in your master CV
                  never appear in your applications. Sift surfaces gaps honestly
                  so you can decide whether to address them, not hide them.
                </p>
              </div>
            </div>
          </Container>
        </section>

        {/* ── Differentiators ──────────────────────────────────────────── */}
        <section className="py-16 md:py-20">
          <Container>
            <div className="mx-auto max-w-2xl divide-y divide-border">
              {[
                {
                  title: "Full-market coverage",
                  desc: "LinkedIn, Indeed, RemoteOK, WeWorkRemotely, Remotive, Hacker News, and more — all in one run. No board left unchecked.",
                },
                {
                  title: "Transparent scoring",
                  desc: "Every match comes with a fit score and a plain-English explanation of why the role ranked where it did.",
                },
                {
                  title: "Cost control",
                  desc: "Set your processing limit before each run. You only pay for the roles you choose to tailor — preview is always free.",
                },
                {
                  title: "Application tracker",
                  desc: "Every application stays in one place: status, notes, and the exact resume version you sent.",
                },
              ].map(({ title, desc }) => (
                <div
                  key={title}
                  className="flex flex-col gap-1 py-5 md:flex-row md:gap-8"
                >
                  <span className="min-w-[200px] font-semibold text-foreground">
                    {title}
                  </span>
                  <span className="text-muted-foreground">{desc}</span>
                </div>
              ))}
            </div>
          </Container>
        </section>

        {/* ── Final CTA ────────────────────────────────────────────────── */}
        <section className="py-20 text-center md:py-28">
          <Container>
            <div className="mx-auto max-w-xl">
              <h2 className="text-3xl font-bold text-foreground md:text-4xl">
                Start with the resume you already have.
              </h2>
              <p className="mt-4 text-muted-foreground">
                Paste in your existing CV, set your criteria, and let Sift find
                the roles that genuinely fit — no blank-slate setup, no invented
                keywords.
              </p>
              <div className="mt-8">
                <Link href="/login">
                  <Button size="lg">Create your account</Button>
                </Link>
              </div>
              <p className="mt-4 text-sm text-muted-foreground">
                No card required to preview matches.
              </p>
            </div>
          </Container>
        </section>
      </main>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <footer className="border-t border-border py-8">
        <Container>
          <div className="flex items-center justify-between">
            <span className="font-semibold text-foreground">Sift</span>
            <span className="text-sm text-muted-foreground">
              &copy; 2026 Sift
            </span>
          </div>
        </Container>
      </footer>
    </div>
  );
}
