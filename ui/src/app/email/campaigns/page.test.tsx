import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EmailCampaignsPage from './page';

const state = vi.hoisted(() => ({
    auth: {
        user: { id: 'user' },
        loading: false,
        getAccessToken: vi.fn(),
        redirectToLogin: vi.fn(),
    },
    listCampaigns: vi.fn(),
    createCampaign: vi.fn(),
    deleteCampaign: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => state.auth }));
vi.mock('@/client', () => ({
    listEmailCampaignsApiV1EmailCampaignsGet: state.listCampaigns,
    createEmailCampaignApiV1EmailCampaignsPost: state.createCampaign,
    deleteEmailCampaignApiV1EmailCampaignsCampaignUuidDelete: state.deleteCampaign,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: vi.fn() }),
    useParams: () => ({}),
}));

const mockCounts = {
    recipient_count: 100,
    eligible_count: 90,
    excluded_no_email_count: 5,
    excluded_invalid_email_count: 2,
    excluded_suppressed_count: 1,
    excluded_opted_out_count: 1,
    excluded_unknown_count: 1,
    duplicates_collapsed_count: 0,
};

const draftCampaign = {
    uuid: 'camp-1',
    name: 'November outreach',
    status: 'draft',
    audience_type: 'leads',
    audience: { audience_type: 'leads', lead_ids: [1, 2] },
    consent_policy: 'opted_in_only',
    consent_override_reason: null,
    consent_override_at: null,
    scheduled_at: null,
    launched_at: null,
    cancelled_at: null,
    failure_reason: null,
    counts: mockCounts,
    sender: null,
    template: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-02T10:00:00Z',
};

const sentCampaign = {
    ...draftCampaign,
    uuid: 'camp-2',
    name: 'October blast',
    status: 'sent',
    launched_at: '2026-10-15T09:00:00Z',
};

beforeEach(() => {
    vi.resetAllMocks();
    state.auth.getAccessToken.mockResolvedValue('access-token');
    state.listCampaigns.mockResolvedValue({ data: [draftCampaign] });
    state.createCampaign.mockResolvedValue({
        data: { ...draftCampaign, uuid: 'camp-new', name: 'New campaign' },
    });
    state.deleteCampaign.mockResolvedValue({ data: null });
});

describe('email campaigns list page', () => {
    it('renders loading skeletons initially', () => {
        state.listCampaigns.mockReturnValue(new Promise(() => {}));
        render(<EmailCampaignsPage />);
        expect(screen.getByRole('status', { name: /loading campaigns/i })).toBeDefined();
    });

    it('renders a campaign from the list', async () => {
        render(<EmailCampaignsPage />);
        expect(await screen.findByText('November outreach')).toBeDefined();
        expect(screen.getByText('Draft')).toBeDefined();
    });

    it('shows empty state when no campaigns', async () => {
        state.listCampaigns.mockResolvedValue({ data: [] });
        render(<EmailCampaignsPage />);
        expect(await screen.findByText('No email campaigns yet')).toBeDefined();
    });

    it('create dialog opens when "New campaign" is clicked and creates successfully', async () => {
        render(<EmailCampaignsPage />);
        await screen.findByText('November outreach');

        fireEvent.click(screen.getByRole('button', { name: 'New campaign' }));
        const dialog = await screen.findByRole('dialog');
        expect(dialog).toBeDefined();

        const input = screen.getByLabelText(/name/i);
        fireEvent.change(input, { target: { value: 'New campaign' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create' }));

        await waitFor(() => expect(state.createCampaign).toHaveBeenCalledTimes(1));
        expect(state.createCampaign.mock.calls[0][0].body.name).toBe('New campaign');
    });

    it('delete button is only shown for draft/ready campaigns', async () => {
        state.listCampaigns.mockResolvedValue({ data: [draftCampaign, sentCampaign] });
        render(<EmailCampaignsPage />);
        await screen.findByText('November outreach');
        await screen.findByText('October blast');

        // draft campaign has delete button, sent does not
        const deleteButtons = screen.getAllByRole('button', { name: /delete november outreach/i });
        expect(deleteButtons.length).toBe(1);
        expect(screen.queryByRole('button', { name: /delete october blast/i })).toBeNull();
    });

    it('delete confirm dialog appears and calls delete API', async () => {
        render(<EmailCampaignsPage />);
        await screen.findByText('November outreach');

        fireEvent.click(screen.getByRole('button', { name: /delete november outreach/i }));
        const confirmDialog = await screen.findByRole('alertdialog');
        expect(confirmDialog).toBeDefined();

        fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(state.deleteCampaign).toHaveBeenCalledTimes(1));
        expect(state.deleteCampaign.mock.calls[0][0].path.campaign_uuid).toBe('camp-1');
    });
});
