from backend.app.intelligence.models import BusinessType, Channel, Intent, PackageTier, RequestedService
from backend.app.intelligence.rules import apply_deterministic_rules

# ---------------------------------------------------------------------------
# INTENT
# ---------------------------------------------------------------------------


def test_intent_greeting():
    assert apply_deterministic_rules("Hello!").intent == Intent.GREETING


def test_intent_pricing():
    assert apply_deterministic_rules("How much does it cost?").intent == Intent.PRICING


def test_intent_services():
    assert apply_deterministic_rules("What services do you offer?").intent == Intent.SERVICES


def test_intent_chatbot():
    assert apply_deterministic_rules("I need a website chatbot.").intent == Intent.CHATBOT


def test_intent_whatsapp():
    assert apply_deterministic_rules("I want a WhatsApp AI agent.").intent == Intent.WHATSAPP


def test_intent_automation():
    assert apply_deterministic_rules("Can you automate my order process?").intent == Intent.AUTOMATION


def test_intent_website_software():
    assert apply_deterministic_rules("I need a new website built.").intent == Intent.WEBSITE_SOFTWARE


def test_intent_demo():
    assert apply_deterministic_rules("Can I see a demo?").intent == Intent.DEMO


def test_intent_human_handoff():
    fields = apply_deterministic_rules("Can I talk to a human?")
    assert fields.intent == Intent.HUMAN_HANDOFF
    assert fields.human_handoff_requested is True


def test_intent_unknown_for_unrecognized_message():
    fields = apply_deterministic_rules("asdlkj qwoeiu random text")
    assert fields.intent is None
    assert fields.confidence == 0.0


# ---------------------------------------------------------------------------
# BUSINESS TYPE
# ---------------------------------------------------------------------------


def test_business_type_dental_clinic():
    assert apply_deterministic_rules("I run a dental clinic.").business_type == BusinessType.DENTAL_CLINIC


def test_business_type_medical_clinic():
    assert apply_deterministic_rules("We operate a medical clinic.").business_type == BusinessType.MEDICAL_CLINIC


def test_business_type_restaurant():
    assert apply_deterministic_rules("I own a restaurant.").business_type == BusinessType.RESTAURANT


def test_business_type_cafe():
    assert apply_deterministic_rules("We run a small cafe.").business_type == BusinessType.CAFE


def test_business_type_other_business_for_unrecognized_specifics():
    fields = apply_deterministic_rules("My company sells furniture.")
    assert fields.business_type == BusinessType.OTHER_BUSINESS


def test_business_type_unknown_when_no_business_mentioned():
    assert apply_deterministic_rules("How much does it cost?").business_type is None


# ---------------------------------------------------------------------------
# REQUESTED SERVICE
# ---------------------------------------------------------------------------


def test_service_chatbot():
    assert apply_deterministic_rules("I need a website chatbot.").requested_service == RequestedService.AI_WEBSITE_CHATBOT


def test_service_whatsapp_ai():
    fields = apply_deterministic_rules("I want a WhatsApp AI agent.")
    assert fields.requested_service == RequestedService.WHATSAPP_AI_AGENT


def test_service_automation():
    fields = apply_deterministic_rules("We need business automation.")
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION


def test_service_website_software():
    fields = apply_deterministic_rules("I need custom software built.")
    assert fields.requested_service in (RequestedService.WEBSITES_SOFTWARE, RequestedService.CUSTOM_SOLUTION)


def test_service_custom_solution():
    fields = apply_deterministic_rules("I'm looking for a custom solution for my business.")
    assert fields.requested_service == RequestedService.CUSTOM_SOLUTION


def test_service_unknown_when_nothing_requested():
    assert apply_deterministic_rules("Hello!").requested_service is None


def test_whatsapp_current_channel_is_not_confused_with_service_request():
    fields = apply_deterministic_rules("We get lots of customer questions on WhatsApp.")
    assert fields.current_channel == Channel.WHATSAPP
    assert fields.requested_service is None


# ---------------------------------------------------------------------------
# PRICING
# ---------------------------------------------------------------------------


def test_pricing_pakistan_request():
    fields = apply_deterministic_rules("How much does it cost in Pakistan?")
    assert fields.intent == Intent.PRICING
    assert fields.country == "Pakistan"
    assert fields.currency == "PKR"


def test_pricing_international_request():
    fields = apply_deterministic_rules("How much would it cost in Dubai?")
    assert fields.intent == Intent.PRICING
    assert fields.country == "United Arab Emirates"
    assert fields.currency == "USD"


def test_pricing_business_package_detection():
    fields = apply_deterministic_rules("How much is the Business package?")
    assert fields.requested_package == PackageTier.BUSINESS


def test_pricing_business_automation_disambiguation_from_business_package():
    package_fields = apply_deterministic_rules("What does the Business package cost?")
    assert package_fields.requested_package == PackageTier.BUSINESS
    assert package_fields.requested_service is None

    automation_fields = apply_deterministic_rules("Can you help with business automation?")
    assert automation_fields.requested_service == RequestedService.BUSINESS_AUTOMATION
    assert automation_fields.requested_package is None


def test_pricing_business_plan_automate_sentence_disambiguates_both():
    fields = apply_deterministic_rules("Can your Business plan automate my business?")
    assert fields.requested_package == PackageTier.BUSINESS
    assert fields.requested_service == RequestedService.BUSINESS_AUTOMATION


def test_country_unknown_is_not_guessed_from_language():
    fields = apply_deterministic_rules("Assalam o alaikum, how much does it cost?")
    assert fields.country is None
    assert fields.currency is None


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------


def test_demo_explicit_request():
    assert apply_deterministic_rules("Can I see a demo?").demo_requested is True


def test_normal_service_question_does_not_trigger_demo():
    fields = apply_deterministic_rules("What does the WhatsApp AI agent do?")
    assert fields.demo_requested is False


# ---------------------------------------------------------------------------
# HUMAN HANDOFF
# ---------------------------------------------------------------------------


def test_human_handoff_explicit_request():
    assert apply_deterministic_rules("I want to talk to a human.").human_handoff_requested is True


def test_normal_question_does_not_trigger_handoff():
    fields = apply_deterministic_rules("What is your Business package price in Pakistan?")
    assert fields.human_handoff_requested is False
