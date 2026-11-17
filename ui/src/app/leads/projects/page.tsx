"use client";

import {
    ArrowRight,
    FolderOpen,
    FolderPlus,
    Loader2,
    Pencil,
    Phone,
    Search,
    Trash2,
    Upload,
    Users,
} from "lucide-react";
import Link from "next/link";
import { type DragEvent,useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost,
    createLeadProjectApiV1LeadProjectsPost,
    deleteLeadProjectApiV1LeadProjectsProjectIdDelete,
    listLeadProjectsApiV1LeadProjectsGet,
    updateLeadProjectApiV1LeadProjectsProjectIdPatch,
} from "@/client/sdk.gen";
import type { LeadProjectResponse } from "@/client/types.gen";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/dateTime";

const LEAD_DRAG_TYPE = "application/x-bolo-lead-ids";

function parseDraggedLeads(event: DragEvent<HTMLElement>): number[] {
    try {
        const raw = event.dataTransfer.getData(LEAD_DRAG_TYPE);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed)
            ? parsed.filter((id): id is number => Number.isInteger(id))
            : [];
    } catch {
        return [];
    }
}

export default function LeadProjectsPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const timezone = useOrganizationTimezone();
    const hasFetched = useRef(false);

    const [projects, setProjects] = useState<LeadProjectResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<number | null>(null);
    const [dropTarget, setDropTarget] = useState<number | null>(null);

    const [createOpen, setCreateOpen] = useState(false);
    const [createName, setCreateName] = useState("");
    const [creating, setCreating] = useState(false);

    const [renaming, setRenaming] = useState<LeadProjectResponse | null>(null);
    const [renameName, setRenameName] = useState("");
    const [renameBusy, setRenameBusy] = useState(false);

    const [deleteTarget, setDeleteTarget] = useState<LeadProjectResponse | null>(null);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    const fetchProjects = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        try {
            const response = await listLeadProjectsApiV1LeadProjectsGet({});
            if (response.error) {
                setError(detailFromError(response.error, "Could not load projects"));
                return;
            }
            setProjects(response.data?.projects ?? []);
        } catch (fetchError) {
            console.error("Failed to load lead projects:", fetchError);
            setError("Could not load projects");
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void fetchProjects();
    }, [loading, user, fetchProjects]);

    const createProject = async () => {
        const name = createName.trim();
        if (!name) return;
        setCreating(true);
        try {
            const response = await createLeadProjectApiV1LeadProjectsPost({
                body: { name },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not create the project"));
                return;
            }
            toast.success(`Created “${name}”`);
            setCreateOpen(false);
            setCreateName("");
            await fetchProjects();
        } catch (createError) {
            console.error("Create project failed:", createError);
            toast.error("Could not create the project");
        } finally {
            setCreating(false);
        }
    };

    const renameProject = async () => {
        if (!renaming) return;
        const name = renameName.trim();
        if (!name) return;
        setRenameBusy(true);
        try {
            const response = await updateLeadProjectApiV1LeadProjectsProjectIdPatch({
                path: { project_id: renaming.id },
                body: { name },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not rename the project"));
                return;
            }
            toast.success("Project renamed");
            setRenaming(null);
            await fetchProjects();
        } catch (renameError) {
            console.error("Rename project failed:", renameError);
            toast.error("Could not rename the project");
        } finally {
            setRenameBusy(false);
        }
    };

    const deleteProject = async () => {
        if (!deleteTarget) return;
        const project = deleteTarget;
        setBusyId(project.id);
        try {
            const response = await deleteLeadProjectApiV1LeadProjectsProjectIdDelete({
                path: { project_id: project.id },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not delete the project"));
                return;
            }
            toast.success(`Deleted “${project.name}” — leads remain in the CRM`);
            setDeleteTarget(null);
            await fetchProjects();
        } catch (deleteError) {
            console.error("Delete project failed:", deleteError);
            toast.error("Could not delete the project");
        } finally {
            setBusyId(null);
        }
    };

    const handleDrop = async (
        project: LeadProjectResponse,
        event: DragEvent<HTMLDivElement>,
    ) => {
        event.preventDefault();
        setDropTarget(null);
        const leadIds = parseDraggedLeads(event);
        if (leadIds.length === 0) return;
        setBusyId(project.id);
        try {
            const response = await assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost({
                path: { project_id: project.id },
                body: { lead_ids: leadIds },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not move the leads"));
                return;
            }
            toast.success(
                `Moved ${response.data?.assigned ?? leadIds.length} lead(s) to “${project.name}”`,
            );
            await fetchProjects();
        } catch (dropError) {
            console.error("Move leads failed:", dropError);
            toast.error("Could not move the leads");
        } finally {
            setBusyId(null);
        }
    };

    const totalLeads = projects.reduce(
        (sum, project) => sum + (project.lead_count ?? 0),
        0,
    );

    return (
        <PageLayout
            title="Lead projects"
            description="Saved collections of CRM leads, ready to export or send to a campaign. A lead lives in one project at a time; moving it never duplicates data."
            actions={
                <>
                    <Button variant="outline" asChild>
                        <Link href="/leads/all">
                            <Users className="h-4 w-4" />
                            CRM
                        </Link>
                    </Button>
                    <Button onClick={() => setCreateOpen(true)}>
                        <FolderPlus className="h-4 w-4" />
                        New project
                    </Button>
                </>
            }
        >
            {error && (
                <p
                    role="alert"
                    className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive"
                >
                    {error}
                </p>
            )}

            <PageSection
                title={
                    <span className="flex items-center gap-2">
                        <FolderOpen className="h-5 w-5 text-cta" />
                        Projects
                        <span className="text-sm font-normal text-muted-foreground">
                            {projects.length} project(s) · {totalLeads} filed lead(s)
                        </span>
                    </span>
                }
                description="Drag leads from a project sheet onto a project card to move them. Deleting a project only unfiles its leads."
                actions={
                    <Button variant="outline" size="sm" asChild>
                        <Link href="/leads/new">
                            <Search className="h-4 w-4" />
                            Grab more leads
                        </Link>
                    </Button>
                }
            >
                {isLoading ? (
                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {[...Array(3)].map((_, index) => (
                            <div key={index} className="h-40 animate-pulse rounded-xl bg-muted" />
                        ))}
                    </div>
                ) : projects.length === 0 ? (
                    <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed py-12 text-center">
                        <div className="rounded-full bg-cta/15 p-4">
                            <FolderPlus className="h-8 w-8 text-cta" />
                        </div>
                        <div className="space-y-1">
                            <h3 className="text-lg font-semibold">No projects yet</h3>
                            <p className="mx-auto max-w-md text-sm text-muted-foreground">
                                Create a project for each campaign list — “US coffee shops”,
                                “UK gyms, no website” — then add leads from the CRM or drag
                                them between projects.
                            </p>
                        </div>
                        <Button onClick={() => setCreateOpen(true)}>
                            <FolderPlus className="h-4 w-4" />
                            Create your first project
                        </Button>
                    </div>
                ) : (
                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {projects.map((project) => {
                            const busy = busyId === project.id;
                            return (
                                <div
                                    key={project.id}
                                    onDragOver={(event) => {
                                        event.preventDefault();
                                        event.dataTransfer.dropEffect = "move";
                                        setDropTarget(project.id);
                                    }}
                                    onDragLeave={(event) => {
                                        if (!event.currentTarget.contains(event.relatedTarget as Node)) {
                                            setDropTarget((current) =>
                                                current === project.id ? null : current,
                                            );
                                        }
                                    }}
                                    onDrop={(event) => void handleDrop(project, event)}
                                    className={[
                                        "flex flex-col gap-3 rounded-xl border bg-card p-4 transition",
                                        dropTarget === project.id
                                            ? "border-cta bg-cta/10 ring-2 ring-cta/40"
                                            : "hover:border-cta/40",
                                    ].join(" ")}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                            <Link
                                                href={`/leads/projects/${project.id}`}
                                                className="truncate text-base font-semibold hover:text-cta hover:underline"
                                            >
                                                {project.name}
                                            </Link>
                                            <p className="mt-0.5 text-xs text-muted-foreground">
                                                Created {formatDate(project.created_at, timezone)}
                                            </p>
                                        </div>
                                        {busy && <Loader2 className="h-4 w-4 animate-spin text-cta" />}
                                    </div>

                                    <div className="flex items-center gap-4 text-sm">
                                        <span className="flex items-center gap-1.5">
                                            <Users className="h-3.5 w-3.5 text-muted-foreground" />
                                            <span className="font-medium tabular-nums">
                                                {project.lead_count ?? 0}
                                            </span>
                                            <span className="text-muted-foreground">leads</span>
                                        </span>
                                        <span className="flex items-center gap-1.5">
                                            <Phone className="h-3.5 w-3.5 text-muted-foreground" />
                                            <span className="font-medium tabular-nums">
                                                {project.callable_count ?? 0}
                                            </span>
                                            <span className="text-muted-foreground">callable</span>
                                        </span>
                                    </div>

                                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                        <Upload className="h-3 w-3 shrink-0" />
                                        Drop leads here to file them in this project
                                    </p>

                                    <div className="mt-auto flex flex-wrap items-center gap-2">
                                        <Button size="sm" asChild>
                                            <Link href={`/leads/projects/${project.id}`}>
                                                Open
                                                <ArrowRight className="h-3.5 w-3.5" />
                                            </Link>
                                        </Button>
                                        <Button
                                            size="icon"
                                            variant="ghost"
                                            aria-label={`Rename ${project.name}`}
                                            title="Rename"
                                            onClick={() => {
                                                setRenaming(project);
                                                setRenameName(project.name);
                                            }}
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </Button>
                                        <Button
                                            size="icon"
                                            variant="ghost"
                                            aria-label={`Delete ${project.name}`}
                                            title="Delete project"
                                            disabled={busy}
                                            onClick={() => setDeleteTarget(project)}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </PageSection>

            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>New project</DialogTitle>
                        <DialogDescription>
                            Projects group leads for one campaign list. You can add leads
                            from the CRM or from another project at any time.
                        </DialogDescription>
                    </DialogHeader>
                    <form
                        className="space-y-3"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void createProject();
                        }}
                    >
                        <div className="space-y-1.5">
                            <Label htmlFor="project-name">Project name</Label>
                            <Input
                                id="project-name"
                                value={createName}
                                onChange={(event) => setCreateName(event.target.value)}
                                placeholder="e.g. US coffee shops — no website"
                                autoFocus
                            />
                        </div>
                        <DialogFooter>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setCreateOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button type="submit" disabled={creating || !createName.trim()}>
                                {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                                Create project
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog
                open={renaming !== null}
                onOpenChange={(open) => {
                    if (!open) setRenaming(null);
                }}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Rename project</DialogTitle>
                        <DialogDescription>
                            Only the list name changes — leads and their CRM data stay as they are.
                        </DialogDescription>
                    </DialogHeader>
                    <form
                        className="space-y-3"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void renameProject();
                        }}
                    >
                        <div className="space-y-1.5">
                            <Label htmlFor="project-rename">Project name</Label>
                            <Input
                                id="project-rename"
                                value={renameName}
                                onChange={(event) => setRenameName(event.target.value)}
                                autoFocus
                            />
                        </div>
                        <DialogFooter>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setRenaming(null)}
                            >
                                Cancel
                            </Button>
                            <Button type="submit" disabled={renameBusy || !renameName.trim()}>
                                {renameBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                                Save name
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog
                open={deleteTarget !== null}
                onOpenChange={(open) => {
                    if (!open) setDeleteTarget(null);
                }}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete this project?</DialogTitle>
                        <DialogDescription>
                            {deleteTarget
                                ? `“${deleteTarget.name}” will be removed. Its ${deleteTarget.lead_count ?? 0} lead(s) stay in the CRM and simply become unfiled.`
                                : ""}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setDeleteTarget(null)}
                            disabled={busyId !== null}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            variant="destructive"
                            onClick={() => void deleteProject()}
                            disabled={busyId !== null}
                        >
                            {busyId !== null && <Loader2 className="h-4 w-4 animate-spin" />}
                            Delete project
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </PageLayout>
    );
}
