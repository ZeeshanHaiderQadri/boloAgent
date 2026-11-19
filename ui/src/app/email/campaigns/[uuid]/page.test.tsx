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
    listRecipients: vi.fn(),
    listCampaigns: vi.fn(),
    previewAudience: vi.fn(),
    launchCampaign: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => state.auth }));
vi.mock('@/client', () => ({
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet: state.getCampaign,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch: state.updateCampaign,
    listEmailSendersApiV1EmailSendersGet: state.listSenders,
    listEmailTemplatesApiV1EmailTemplatesGet: state.listTemplates,
    listCampaignRecipientsApiV1EmailCampaignsCampaignUuidRecipientsGet: state.listRecipients,
    listEmailCampaignsApiV1EmailCampaignsGet: state.listCampaigns,
    previewCampaignAudienceApiV1EmailCampaignsPreviewPost: state.previewAudience,
    launchEmailCampaignApiV1EmailCampaignsCampaignUuidLaunchPost: state.launchCampaign,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: vi.fn() }),
    useParams: () => ({ uuid: 'camp-1' }),
}));
vi.mock('next/link', () => ({
    default: ({ children, href }: { children: React.ReactNode; href: string }) => (
        <a href={href}>{children}</a>
    ),
}));

const mockCounts = {
    recipient_count: 100,
    eligible_count: 90,
    excluded_no_email_count: 5,
    excluded_invalid_email_count: 2,
    excluded_suppressed_count: 1,
    excluded_opted_out_count: 1,
    excluded_unknown_count: 1,
    excluded_manual_count: 0,
    duplicates_collapsed_count: 0,
};

const freshCampaign = {
    uuid: 'camp-1',
    name: 'November outreach',
    status: 'draft',
    audience_type: 'leads',
    audience: { lead_ids: [1, 2] },
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

const campaignWithSenderAndTemplate = {
    ...freshCampaign,
    sender: {
        uuid: 'sender-1',
        name: 'Primary sender',
        status: 'verified',
        from_name: 'Acme',
        from_email: 'hello@acme.com',
        reply_to_email: null,
        daily_cap: 1000,
        rate_per_second: 10,
        estimated_days: 1,
        guidance: '1000 per day, this campaign will take 1 day.',
        limits: { daily_cap: 1000, rate_per_second: 10 },
    },
    template: {
        uuid: 'tmpl-1',
        name: 'Welcome email',
        subject: 'Hello {{business_name}}',
        source_format: 'html',
        has_unsubscribe_placeholder: true,
        unsubscribe_warning: null,
        created_at: '2026-11-01T10:00:00Z',
        updated_at: null,
    },
};

const mockSenders = [
    {
        uuid: 'sender-1',
        name: 'Primary sender',
        status: 'verified',
        from_name: 'Acme',
        from_email: 'hello@acme.com',
        reply_to_email: null,
        daily_cap: 1000,
        rate_per_second: 10,
        estimated_days: 1,
        guidance: '1000 per day.',
        limits: { daily_cap: 1000, rate_per_second: 10 },
        provider: 'smtp',
    },
];

const mockTemplates = [
    {
        uuid: 'tmpl-1',
        name: 'Welcome email',
        subject: 'Hello {{business_name}}',
        source_format: 'html',
        has_unsubscribe_placeholder: true,
        created_at: '2026-11-01T10:00:00Z',
        updated_at: null,
    },
];

const mockRecipientsData = {
    items: [
        {
            lead_id: 1,
            title: 'Alice Corp',
            email: 'alice@acme.com',
            email_consent: 'opted_in',
            bucket: 'eligible',
            reason: 'Consent recorded — will receive this campaign.',
        },
    ],
    page: 1,
    limit: 20,
    total: 1,
    totals_per_bucket: {
        eligible: 1,
        no_email: 0,
        invalid_email: 0,
        suppressed: 0,
        opted_out: 0,
        unknown: 0,
        manually_excluded: 0,
    },
};

beforeEach(() => {
    vi.resetAllMocks();
    state.auth.getAccessToken.mockResolvedValue('access-token');
    state.getCampaign.mockResolvedValue({ data: freshCampaign });
    state.listSenders.mockResolvedValue({ data: mockSenders });
    state.listTemplates.mockResolvedValue({ data: mockTemplates });
    state.listRecipients.mockResolvedValue({ data: mockRecipientsData });
    state.updateCampaign.mockResolvedValue({ data: freshCampaign });
    state.listCampaigns.mockResolvedValue({ data: [] });
    state.previewAudience.mockResolvedValue({ data: { counts: mockCounts, masked_sample: [], unknown_lead_count: 0, warnings: [] } });
});

/** Click the "Next" button inside the wizard content area. */
function clickNext() {
    const nextButtons = screen.getAllByRole('button', { name: /next/i });
    fireEvent.click(nextButtons[nextButtons.length - 1]);
}

/** Wait until a specific step heading appears (1-indexed: Step 1, Step 2, ...). */
async function atStep(stepNum: number) {
    await screen.findByRole('heading', { name: new RegExp(`Step ${stepNum}`, 'i') });
}

describe('email campaign wizard', () => {
    it('renders step 1 (audience) on load for a fresh campaign', async () => {
        render(<EmailCampaignDetailPage />);
        await atStep(1);
        expect(screen.getByText('November outreach')).toBeDefined();
    });

    it('"Next" on step 1 advances to step 2', async () => {
        render(<EmailCampaignDetailPage />);
        await atStep(1);

        clickNext();
        await atStep(2);
    });

    it('step 3 consent: switching to include-unknown shows the reason textarea', async () => {
        render(<EmailCampaignDetailPage />);
        await atStep(1);

        clickNext();
        await atStep(2);

        clickNext();
        await atStep(3);

        // Click the "Include unknown consent" card
        fireEvent.click(screen.getByText('Include unknown consent'));

        // The reason textarea should now be visible
        expect(await screen.findByRole('textbox')).toBeDefined();
        // "Written reason" appears as a label for the textarea
        const reasonLabels = screen.getAllByText(/Written reason/i);
        expect(reasonLabels.length).toBeGreaterThan(0);
    });

    it('step 3 consent: clicking Next without a reason shows a field-level error and does not advance', async () => {
        render(<EmailCampaignDetailPage />);
        await atStep(1);

        clickNext();
        await atStep(2);

        clickNext();
        await atStep(3);

        // Switch to include-unknown
        fireEvent.click(screen.getByText('Include unknown consent'));
        await screen.findByRole('textbox');

        // Click Next without filling in the reason
        clickNext();

        // Field-level error should appear (role=alert)
        await waitFor(() => {
            const alerts = screen.getAllByRole('alert');
            expect(alerts.length).toBeGreaterThan(0);
        });
        const alertText = screen.getAllByRole('alert').map((el) => el.textContent).join(' ');
        expect(alertText).toMatch(/reason/i);

        // Still on step 3 (heading present)
        expect(screen.getByRole('heading', { name: /Step 3/i })).toBeDefined();
    });

    it('step 5 review: launch button calls the launch endpoint', async () => {
        // Campaign with sender+template resumes at step 2 (heading "Step 3")
        state.launchCampaign.mockResolvedValue({
            data: {
                status: 'sending',
                ready: true,
                preconditions: [],
                message: 'Campaign launched: 90 recipient(s) queued for delivery.',
                queued_count: 90,
            },
        });
        state.getCampaign.mockResolvedValue({ data: campaignWithSenderAndTemplate });
        render(<EmailCampaignDetailPage />);

        // Wait for the page to load and settle at step 2
        await screen.findByText('November outreach');
        await atStep(3); // Step 2 renders Step3Consent (heading "Step 3")

        // Step 2 -> 3 (Consent -> Recipients)
        clickNext();
        await atStep(4);

        // Step 3 -> 4 (Recipients -> Review)
        clickNext();
        await atStep(5);

        const launchButton = screen.getByRole('button', { name: /launch campaign/i });
        expect((launchButton as HTMLButtonElement).disabled).toBe(false);

        fireEvent.click(launchButton);
        await waitFor(() => expect(state.launchCampaign).toHaveBeenCalled());
        expect(state.launchCampaign.mock.calls[0][0]).toMatchObject({
            path: { campaign_uuid: 'camp-1' },
        });
    });

    it('recipients step: renders a row from the mocked recipients endpoint', async () => {
        state.getCampaign.mockResolvedValue({ data: campaignWithSenderAndTemplate });
        render(<EmailCampaignDetailPage />);

        await screen.findByText('November outreach');
        await atStep(3); // Step 2 renders Step3Consent

        // Step 2 -> 3 (Consent -> Recipients)
        clickNext();
        await atStep(4);

        // Recipient row should appear from the mocked endpoint
        expect(await screen.findByText('Alice Corp')).toBeDefined();
        // The operator sees the address the campaign resolves.
        expect(screen.getByText('alice@acme.com')).toBeDefined();
        expect(screen.getByText(/Consent recorded/)).toBeDefined();
    });

    it('auto-save: PATCH is called when advancing past step 2 with a sender selected', async () => {
        render(<EmailCampaignDetailPage />);
        await atStep(1);

        // Go to step 2
        clickNext();
        await atStep(2);

        // Click Next — should trigger autoSave with sender_uuid
        clickNext();

        await waitFor(() => expect(state.updateCampaign).toHaveBeenCalledTimes(1));
        const callBody = state.updateCampaign.mock.calls[0][0].body;
        expect('sender_uuid' in callBody).toBe(true);
        expect('template_uuid' in callBody).toBe(true);
    });

    it('auto-preview: filter audiences send the stored filters without all_matching', async () => {
        state.getCampaign.mockResolvedValue({
            data: {
                ...campaignWithSenderAndTemplate,
                audience_type: 'filter',
                audience: { filters: { city: 'Austin', has_email: true, all_matching: true }, all_matching: true },
            },
        });
        render(<EmailCampaignDetailPage />);

        await screen.findByText('November outreach');
        await atStep(3); // resumes at consent

        clickNext();

        await waitFor(() => expect(state.previewAudience).toHaveBeenCalledTimes(1));
        const body = state.previewAudience.mock.calls[0][0].body;
        expect(body.audience.audience_type).toBe('filter');
        expect(body.audience.all_matching).toBe(true);
        expect(body.audience.filters).toEqual({ city: 'Austin', has_email: true });
        expect(state.previewAudience.mock.calls[0][0].body.audience.filters).not.toHaveProperty('all_matching');
    });

    it('recipients step: initial fetch asks for all buckets instead of an empty bucket', async () => {
        state.getCampaign.mockResolvedValue({ data: campaignWithSenderAndTemplate });
        render(<EmailCampaignDetailPage />);

        await screen.findByText('November outreach');
        await atStep(3);

        clickNext();
        await atStep(4);

        await waitFor(() => expect(state.listRecipients).toHaveBeenCalled());
        const query = state.listRecipients.mock.calls[0][0].query;
        expect(query.bucket).toBeUndefined();
    });
});
