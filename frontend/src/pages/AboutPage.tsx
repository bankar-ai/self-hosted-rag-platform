import { CAN_DO, CANNOT_DO } from "../lib/introContent";

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-1 text-2xl font-semibold text-slate-900">How it works</h1>
      <p className="mb-6 text-sm text-slate-500">
        A quick guide to what this tool does -- and doesn't do.
      </p>

      <h2 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
        You can
      </h2>
      <ul className="mb-6 list-disc space-y-1 pl-5 text-sm text-slate-700">
        {CAN_DO.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>

      <h2 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
        Good to know
      </h2>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
        {CANNOT_DO.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
