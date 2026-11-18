import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EmailTemplatesPage from './page';

const state = vi.hoisted(() => ({
    auth: { user: { id: 'user' }, loading: false, getAccessToken: vi.fn(), redirectToLogin: vi.fn() },
    listTemplates: vi.fn(),
    createTemplate: vi.fn(),
    updateTemplate: vi.fn(),
    getTemplate: vi.fn(),
    previewTemplate: vi.fn(),
    testSendTemplate: vi.fn(),
    duplicateTemplate: vi.fn(),
    deleteTemplate: vi.fn(),
    mergeFields: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => state.auth }));
vi.mock('@/client', () => ({
    listEmailTemplatesApiV1EmailTemplatesGet: state.listTemplates,
    createEmailTemplateApiV1EmailTemplatesPost: state.createTemplate,
    updateEmailTemplateApiV1EmailTemplatesTemplateUuidPatch: state.updateTemplate,
    getEmailTemplateApiV1EmailTemplatesTemplateUuidGet: state.getTemplate,
    previewEmailTemplateApiV1EmailTemplatesPreviewPost: state.previewTemplate,
    testSendEmailTemplateApiV1EmailTemplatesTemplateUuidTestSendPost: state.testSendTemplate,
    duplicateEmailTemplateApiV1EmailTemplatesTemplateUuidDuplicatePost: state.duplicateTemplate,
    deleteEmailTemplateApiV1EmailTemplatesTemplateUuidDelete: state.deleteTemplate,
    listEmailTemplateMergeFieldsApiV1EmailTemplatesMergeFieldsGet: state.mergeFields,
}));

const templateSummary = {
    uuid: 'template-1',
    name: 'Welcome email',
    subject: 'Hello {{business_name}}',
    source_format: 'html',
    has_unsubscribe_placeholder: false,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-02T10:00:00Z',
};

const templateResponse = {
    ...templateSummary,
    source: '<p>Hi {{business_name}}</p>',
    html_body: '<p>Hi Acme Demo Co.</p>',
    text_body: 'Hi Acme Demo Co.',
    merge_fields: ['business_name'],
    warnings: [],
};

const mergeFieldRows = [
    {
        name: 'business_name',
        label: 'Business name',
        description: 'The lead business name.',
        fallback: 'there',
        sample: 'Acme Demo Co.',
        required: false,
    },
    {
        name: 'unsubscribe_link',
        label: 'Unsubscribe link',
        description: 'Personal unsubscribe link.',
        fallback: '',
        sample: 'https://boloagent.com/unsubscribe/preview-sample',
        required: true,
    },
];

beforeEach(() => {
    vi.resetAllMocks();
    state.auth.getAccessToken.mockResolvedValue('access-token');
    state.listTemplates.mockResolvedValue({ data: [templateSummary] });
    state.mergeFields.mockResolvedValue({ data: mergeFieldRows });
    state.getTemplate.mockResolvedValue({ data: templateResponse });
    state.previewTemplate.mockResolvedValue({
        data: {
            subject: 'Hello Acme Demo Co.',
            html: '<p>Hi Acme Demo Co.</p>',
            text: 'Hi Acme Demo Co.',
            has_unsubscribe_placeholder: true,
            warnings: [],
        },
    });
    state.createTemplate.mockResolvedValue({
        data: { ...templateResponse, uuid: 'template-new', name: 'Cold outreach' },
    });
});

function fieldByLabel(dialog: ReturnType<typeof within>, label: string) {
    return dialog.getByLabelText(label);
}

describe('email templates page', () => {
    it('lists templates and flags one without an unsubscribe placeholder', async () => {
        render(<EmailTemplatesPage />);

        expect(await screen.findByText('Welcome email')).toBeDefined();
        expect(screen.getByText('No unsubscribe link')).toBeDefined();
        expect(
            screen.getByText(/One template has no unsubscribe placeholder/)
        ).toBeDefined();
    });

    it('creates an MJML template from the editor', async () => {
        render(<EmailTemplatesPage />);
        await screen.findByText('Welcome email');

        fireEvent.click(screen.getByRole('button', { name: 'New template' }));
        const dialog = within(await screen.findByRole('dialog'));

        fireEvent.change(fieldByLabel(dialog, 'Name *'), {
            target: { value: 'Cold outreach' },
        });
        fireEvent.change(fieldByLabel(dialog, 'Subject line *'), {
            target: { value: 'Quick question' },
        });
        fireEvent.click(dialog.getByRole('button', { name: 'MJML' }));
        fireEvent.change(fieldByLabel(dialog, 'MJML source *'), {
            target: { value: '<mjml><mj-body><mj-section><mj-column><mj-text>Hi</mj-text></mj-column></mj-section></mj-body></mjml>' },
        });
        fireEvent.click(dialog.getByRole('button', { name: 'Save' }));

        await waitFor(() => expect(state.createTemplate).toHaveBeenCalledTimes(1));
        expect(state.createTemplate.mock.calls[0][0].body).toEqual({
            name: 'Cold outreach',
            subject: 'Quick question',
            source_format: 'mjml',
            source: '<mjml><mj-body><mj-section><mj-column><mj-text>Hi</mj-text></mj-column></mj-section></mj-body></mjml>',
        });
        expect(await screen.findByText('Cold outreach')).toBeDefined();
    });

    it('renders the server preview inside sandboxed desktop and mobile frames', async () => {
        render(<EmailTemplatesPage />);
        await screen.findByText('Welcome email');

        fireEvent.click(screen.getByRole('button', { name: 'Welcome email' }));
        const dialog = within(await screen.findByRole('dialog'));
        await waitFor(() =>
            expect((fieldByLabel(dialog, 'Name *') as HTMLInputElement).value).toBe(
                'Welcome email'
            )
        );

        fireEvent.click(dialog.getByRole('button', { name: 'Refresh' }));
        await waitFor(() => expect(state.previewTemplate).toHaveBeenCalledTimes(1));

        const desktop = await dialog.findByTitle('Desktop preview');
        const mobile = dialog.getByTitle('Mobile preview');
        expect(desktop.getAttribute('sandbox')).toBe('');
        expect(mobile.getAttribute('sandbox')).toBe('');
        expect(desktop.getAttribute('srcdoc')).toContain('Hi Acme Demo Co.');
        expect(dialog.queryByText(/Unsubscribe link missing/)).toBeNull();
        expect(dialog.getByText('Unsubscribe link present')).toBeDefined();
    });

    it('shows the server rejection when a template uses an unknown merge field', async () => {
        state.createTemplate.mockResolvedValue({
            error: { detail: 'Unknown merge field: first_name. Supported fields: business_name, unsubscribe_link' },
        });
        render(<EmailTemplatesPage />);
        await screen.findByText('Welcome email');

        fireEvent.click(screen.getByRole('button', { name: 'New template' }));
        const dialog = within(await screen.findByRole('dialog'));
        fireEvent.change(fieldByLabel(dialog, 'Name *'), {
            target: { value: 'Bad template' },
        });
        fireEvent.click(dialog.getByRole('button', { name: 'Save' }));

        expect((await dialog.findByRole('alert')).textContent).toContain(
            'Unknown merge field: first_name'
        );
    });

    it('enables Send test only after the template is saved, and targets only the template', async () => {
        render(<EmailTemplatesPage />);
        await screen.findByText('Welcome email');

        fireEvent.click(screen.getByRole('button', { name: 'New template' }));
        const dialog = within(await screen.findByRole('dialog'));
        expect(
            (dialog.getByRole('button', { name: 'Send test' }) as HTMLButtonElement).disabled
        ).toBe(true);

        fireEvent.change(fieldByLabel(dialog, 'Name *'), {
            target: { value: 'Cold outreach' },
        });
        fireEvent.click(dialog.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(state.createTemplate).toHaveBeenCalledTimes(1));

        state.testSendTemplate.mockResolvedValue({
            data: {
                provider_message_id: 'fake-provider-id',
                to_email: 'owner@apex-software-demo.com',
                message: 'Test message accepted and sent to your own account address. No lead was contacted.',
            },
        });
        const sendButton = await dialog.findByRole('button', { name: 'Send test' });
        await waitFor(() => expect((sendButton as HTMLButtonElement).disabled).toBe(false));
        fireEvent.click(sendButton);

        await waitFor(() => expect(state.testSendTemplate).toHaveBeenCalledTimes(1));
        expect(state.testSendTemplate.mock.calls[0][0]).toEqual({
            headers: { Authorization: 'Bearer access-token' },
            path: { template_uuid: 'template-new' },
            body: {},
        });
    });
});
