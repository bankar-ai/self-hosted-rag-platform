import { Button } from "@/components/ui/button";
import CopyButton from "./CopyButton";

export interface SidebarConversation {
  id: string;
  title: string;
}

export interface SidebarDocument {
  id: string;
  title: string;
}

interface SidebarProps {
  recentConversations: SidebarConversation[];
  activeConversationId: string;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (conv: SidebarConversation) => void;
  /** Fetches and formats one conversation's transcript on demand (ERP-075) -- the sidebar only
   * ever holds an id/title, so copying a row that isn't the currently-open conversation needs
   * a fetch, unlike the open chat's own "Copy conversation" action. */
  onCopyTranscript: (id: string) => Promise<string>;
  onNewConversation: () => void;
  documents: SidebarDocument[];
  deselectedDocumentIds: Set<string>;
  onToggleDocument: (id: string) => void;
  /** ERP-078: below the `md` breakpoint the sidebar becomes a slide-in overlay instead of a
   * static column, since a fixed 64/80/rest three-column layout doesn't fit a narrow viewport. */
  isOpenOnMobile: boolean;
  onCloseMobile: () => void;
}

/** Recent conversations + documents-with-checkboxes (ERP-044); the left pane of the chat's
 * three-pane layout (ERP-050). Extracted from `ChatPage.tsx` unchanged in behavior. */
export default function Sidebar({
  recentConversations,
  activeConversationId,
  onSelectConversation,
  onRenameConversation,
  onCopyTranscript,
  onNewConversation,
  documents,
  deselectedDocumentIds,
  onToggleDocument,
  isOpenOnMobile,
  onCloseMobile,
}: SidebarProps) {
  return (
    <>
      {isOpenOnMobile && (
        <div
          className="fixed inset-0 z-20 bg-black/30 md:hidden"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}
      <aside
        className={`${isOpenOnMobile ? "flex" : "hidden"} fixed inset-y-0 left-0 z-30 w-64 shrink-0 flex-col overflow-y-auto border-r border-slate-200 bg-slate-50 p-4 md:static md:z-auto md:flex`}
      >
        <button
          type="button"
          className="mb-2 self-end rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 md:hidden"
          onClick={onCloseMobile}
          aria-label="Close sidebar"
        >
          ✕
        </button>
        <Button
          className="mb-4 w-full"
          onClick={() => {
            onNewConversation();
            onCloseMobile();
          }}
        >
          New chat
        </Button>
      <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Recent conversations
      </p>
      {recentConversations.length === 0 ? (
        <p className="px-1 text-sm text-slate-400">No conversations yet.</p>
      ) : (
        <ul className="mb-6 flex flex-col gap-1">
          {recentConversations.map((conv) => (
            <li key={conv.id} className="flex items-center gap-1">
              <button
                className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-slate-200 ${
                  conv.id === activeConversationId
                    ? "bg-brand/10 font-medium text-brand-dark"
                    : "text-slate-700"
                }`}
                onClick={() => {
                  onSelectConversation(conv.id);
                  onCloseMobile();
                }}
              >
                {conv.title}
              </button>
              <button
                className="shrink-0 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                title="Rename conversation"
                onClick={() => onRenameConversation(conv)}
              >
                ✎
              </button>
              <CopyButton
                getText={() => onCopyTranscript(conv.id)}
                label="⧉"
                title="Copy conversation"
                className="shrink-0 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700"
              />
            </li>
          ))}
        </ul>
      )}
      <p className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Your documents
      </p>
      {documents.length === 0 ? (
        <p className="px-1 text-sm text-slate-400">
          No documents uploaded yet — visit Documents to add one.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {documents.map((doc) => {
            const isSelected = !deselectedDocumentIds.has(doc.id);
            return (
              <li key={doc.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-200">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 shrink-0 accent-brand"
                    checked={isSelected}
                    onChange={() => onToggleDocument(doc.id)}
                  />
                  <span className="truncate" title={doc.title}>
                    {doc.title}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {documents.length > 0 && (
        <p className="mt-1 px-2 text-xs text-slate-400">
          Answers are grounded only in checked documents.
        </p>
      )}
      {documents.length > 0 && deselectedDocumentIds.size === documents.length && (
        <p className="mt-1 px-2 text-xs text-amber-600">
          No documents selected — questions won&apos;t find any answers.
        </p>
      )}
      </aside>
    </>
  );
}
