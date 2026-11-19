import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EmailCampaignDetailPage from './page';

const state = vi.hoisted(() => ({
    auth: {
        user: { id: 'user' },
        loading: false,
        getAccessToken: vi.fn(),
        redirectToLogin: vi.fn(),
    },
    getCampaign: vi.fn(),
    updateCampaign: vi.fn(),
    listSenders: vi.fn(),
    listTemplates: vi.fn(),
    previewAudience: vi.fn(),
    launchCampaign: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => state.auth }));
vi.mock('@/client', () => ({
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet: state.getCampaign,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch: state.updateCampaign,
    listEmailSendersApiV1EmailSendersGet: state.listSenders,
    listEmailTemplatesApiV1EmailTemplatesGet: state.listTemplates,
    previewCampaignAudienceApiV1EmailCampaignsPreviewPost: state.previewAudience,
    launchEmailCampaignApiV1EmailCampaignsCampaignUuidLaunchPost: state.launchCampaign,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: vi.fn() }),
    useParams: () => ({ uuid: 'camp-1' }),
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

const mockSender = {
    uuid: 'sender-1',
    name: 'Primary sender',
    status: 'verified',
    from_name: 'Acme',
    from_email: 'hello@acme.com',
    reply_to_email: null,
    daily_cap: 1000,
    rate_per_second: 10,
    estimated_days: 1,
    guidance: 'At 1000/day this campaign will finish in roughly 1 day.',
};

const mockTemplate = {
    uuid: 'tmpl-1',
    name: 'Welcome email',
    subject: 'Hello {{business_name}}',
    source_format: 'html',
    has_unsubscribe_placeholder: true,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: null,
};

const previewData = {
    counts: {
        recipient_count: 50,
        eligible_count: 45,
        excluded_no_email_count: 2,
        excluded_invalid_email_count: 1,
        excluded_suppressed_count: 1,
        excluded_opted_out_count: 0,
        excluded_unknown_count: 1,
        duplicates_collapsed_count: 0,
    },
    masked_sample: ['a***@example.com', 'b***@demo.org'],
    unknown_lead_count: 0,
    warnings: [],
};

beforeEach(() => {
    vi.resetAllMocks();
    state.auth.getAccessToken.mockResolvedValue('access-token');
    state.getCampaign.mockResolvedValue({ data: draftCampaign });
    state.listSenders.mockResolvedValue({ data: [mockSender] });
    state.listTemplates.mockResolvedValue({ data: [mockTemplate] });
    state.previewAudience.mockResolvedValue({ data: previewData });
    state.launchCampaign.mockResolvedValue({
        data: { status: 'sending', ready: true, preconditions: [], message: 'Launched' },
    });
});

describe('email campaign detail page', () => {
    it('renders loading skeletons initially', () => {
        state.getCampaign.mockReturnValue(new Promise(() => {}));
        state.listSenders.mockReturnValue(new Promise(() => {}));
        state.listTemplates.mockReturnValue(new Promise(() => {}));
        render(<EmailCampaignDetailPage />);
        expect(screen.getByRole('status', { name: /loading campaign/i })).toBeDefined();
    });

    it('renders campaign name and status badge', async () => {
        render(<EmailCampaignDetailPage />);
        expect(await screen.findByText('November outreach')).toBeDefined();
        expect(screen.getByText('Draft')).toBeDefined();
    });

    it('shows sender guidance when a sender is selected', async () => {
        state.getCampaign.mockResolvedValue({
            data: {
                ...draftCampaign,
                sender: mockSender,
            },
        });
        render(<EmailCampaignDetailPage />);
        expect(
            await screen.findByText(/At 1000\/day this campaign will finish in roughly 1 day/)
        ).toBeDefined();
    });

    it('launch button is permanently disabled', async () => {
        render(<EmailCampaignDetailPage />);
        await screen.findByText('November outreach');

        const launchButton = screen.getByRole('button', { name: 'Launch' });
        expect((launchButton as HTMLButtonElement).disabled).toBe(true);
        // Verify the launch API is never called
        expect(state.launchCampaign).not.toHaveBeenCalled();
    });

    it('preview button triggers preview API and shows counts', async () => {
        render(<EmailCampaignDetailPage />);
        await screen.findByText('November outreach');

        fireEvent.click(screen.getByRole('button', { name: /preview audience/i }));

        await waitFor(() => expect(state.previewAudience).toHaveBeenCalledTimes(1));

        // Should NOT pass campaign_uuid (deprecated field)
        const callBody = state.previewAudience.mock.calls[0][0].body;
        expect(callBody.campaign_uuid).toBeUndefined();

        // Shows counts
        expect(await screen.findByText('50')).toBeDefined();

        // Shows masked sample
        expect(screen.getByText(/Masked sample/)).toBeDefined();
        expect(screen.getByText('a***@example.com')).toBeDefined();
    });

    it('non-draft campaign shows "can no longer be edited" notice', async () => {
        state.getCampaign.mockResolvedValue({
            data: { ...draftCampaign, status: 'sent', launched_at: '2026-11-10T09:00:00Z' },
        });
        render(<EmailCampaignDetailPage />);
        expect(
            await screen.findByText(/can no longer be edited/i)
        ).toBeDefined();
    });
});
