import logging
import re
import urllib
import uuid
import datetime

from dimagi.utils.couch.database import get_db
from corehq.apps.users.models import CouchUser
from django.template.loader import render_to_string
from corehq.apps.hqcase.utils import submit_case_blocks

from xml.etree.ElementTree import XML, tostring
from dimagi.utils.parsing import json_format_datetime

def strip_plus(phone_number):
    if isinstance(phone_number, basestring) and phone_number[0] == "+":
        return phone_number[1:]
    else:
        return phone_number

def clean_phone_number(text):
    """
    strip non-numeric characters and add '%2B' at the front
    """
    non_decimal = re.compile(r'[^\d.]+')
    plus = '+'
    cleaned_text = "%s%s" % (plus, non_decimal.sub('', text))
    return cleaned_text

def clean_outgoing_sms_text(text):
    try:
        return urllib.quote(text)
    except KeyError:
        return urllib.quote(text.encode('utf-8'))

def domains_for_phone(phone):
    """
    Get domains attached to a phone number
    """
    view_results = get_db().view("sms/phones_to_domains", key=phone)
    return [row["value"] for row in view_results]

def users_for_phone(phone):
    """
    Get users attached to a phone number
    """
    view_results = get_db().view("sms/phones_to_domains", key=phone)
    user_ids = set([row["id"] for row in view_results])
    return [CouchUser.get(id) for id in user_ids]


def format_message_list(message_list):
    """
    question = message_list[-1]
    if len(question) > 160:
        return question[0:157] + "..."
    else:
        extra_space = 160 - len(question)
        message_start = ""
        if extra_space > 3:
            for msg in message_list[0:-1]:
                message_start += msg + ". "
            if len(message_start) > extra_space:
                message_start = message_start[0:extra_space-3] + "..."
        return message_start + question
    """
    # Some gateways (yo) allow a longer message to be sent and handle splitting it up on their end, so for now just join all messages together
    return " ".join(message_list)

def submit_xml(domain, template, context):
    case_block = render_to_string(template, context)
    case_block = tostring(XML(case_block)) # Ensure the XML is formatted properly, an exception is raised if not
    submit_case_blocks(case_block, domain)

# Creates a case by submitting system-generated casexml
def register_sms_contact(domain, case_type, case_name, user_id, contact_phone_number, contact_phone_number_is_verified="1", contact_backend_id=None, language_code=None, time_zone=None, owner_id=None):
    utcnow = str(datetime.datetime.utcnow())
    case_id = str(uuid.uuid3(uuid.NAMESPACE_URL, utcnow))
    if owner_id is None:
        owner_id = user_id
    context = {
        "case_id" : case_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "case_type" : case_type,
        "case_name" : case_name,
        "owner_id" : owner_id,
        "user_id" : user_id,
        "contact_phone_number" : contact_phone_number,
        "contact_phone_number_is_verified" : contact_phone_number_is_verified,
        "contact_backend_id" : contact_backend_id,
        "language_code" : language_code,
        "time_zone" : time_zone
    }
    submit_xml(domain, "sms/xml/register_contact.xml", context)
    return case_id

def update_contact(domain, case_id, user_id, contact_phone_number=None, contact_phone_number_is_verified=None, contact_backend_id=None, language_code=None, time_zone=None):
    context = {
        "case_id" : case_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "user_id" : user_id,
        "contact_phone_number" : contact_phone_number,
        "contact_phone_number_is_verified" : contact_phone_number_is_verified,
        "contact_backend_id" : contact_backend_id,
        "language_code" : language_code,
        "time_zone" : time_zone
    }
    submit_xml(domain, "sms/xml/update_contact.xml", context)

def create_task(parent_case, submitting_user_id, task_owner_id, form_unique_id, task_activation_datetime, task_deactivation_datetime, incentive):
    utcnow = str(datetime.datetime.utcnow())
    subcase_guid = str(uuid.uuid3(uuid.NAMESPACE_URL, utcnow))
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "task_owner_id" : task_owner_id,
        "form_unique_id" : form_unique_id,
        "task_activation_date" : json_format_datetime(task_activation_datetime),
        "task_deactivation_date" : json_format_datetime(task_deactivation_datetime),
        "parent" : parent_case,
        "incentive" : incentive,
    }
    submit_xml(parent_case.domain, "sms/xml/create_task.xml", context)
    return subcase_guid

def update_task(domain, subcase_guid, submitting_user_id, task_owner_id, form_unique_id, task_activation_datetime, task_deactivation_datetime, incentive):
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
        "task_owner_id" : task_owner_id,
        "form_unique_id" : form_unique_id,
        "task_activation_date" : json_format_datetime(task_activation_datetime),
        "task_deactivation_date" : json_format_datetime(task_deactivation_datetime),
        "incentive" : incentive,
    }
    submit_xml(domain, "sms/xml/update_task.xml", context)

def close_task(domain, subcase_guid, submitting_user_id):
    context = {
        "subcase_guid" : subcase_guid,
        "user_id" : submitting_user_id,
        "date_modified" : json_format_datetime(datetime.datetime.utcnow()),
    }
    submit_xml(domain, "sms/xml/close_task.xml", context)

def create_billable_for_sms(msg, backend_api, delay=True, **kwargs):
    try:
        from hqbilling.tasks import bill_client_for_sms
        from hqbilling.models import API_TO_BILLABLE
        billable_class = API_TO_BILLABLE.get(backend_api)
        if delay:
            bill_client_for_sms.delay(billable_class, msg._id, **kwargs)
        else:
            bill_client_for_sms(billable_class, msg._id, **kwargs)
    except Exception as e:
        logging.error("%s backend contacted, but errors in creating billable for incoming message. Error: %s" %
                      (backend_api, e))
