##
## Created       : Wed May 18 13:16:17 IST 2011
## Last Modified : Tue Apr 03 19:17:16 IST 2012
##
## Copyright (C) 2011, 2012 Sriram Karra <karra.etc@gmail.com>
##
## Licensed under the GPL v3
##

import sys, os, logging, time, traceback
import iso8601, base64
import utils

if __name__ == "__main__":
    ## Being able to fix the sys.path thusly makes is easy to execute this
    ## script standalone from IDLE. Hack it is, but what the hell.
    DIR_PATH    = os.path.abspath(os.path.dirname(os.path.realpath('../Gout')))
    EXTRA_PATHS = [os.path.join(DIR_PATH, 'lib')]
    sys.path = EXTRA_PATHS + sys.path

from   abc            import ABCMeta, abstractmethod
from   folder         import Folder
from   win32com.mapi  import mapi, mapitags, mapiutil
from   contact_ol     import OLContact

class OLFolder(Folder):
    """An Outlook folder directly corresponds to a MAPI Folder entity. This
    class wraps a mapi folder object while implementing the normalized Folder
    methods and accessors defined by Gout.

    This itself is an abstract class, and only its derived classes can be
    instantiated.
    """

    __metaclass__ = ABCMeta

    def __init__ (self, db, entryid, name, fobj, msgstore):
        Folder.__init__(self, db)

        self.set_entryid(entryid)
        self.set_name(name)
        self.set_fobj(fobj)
        self.set_msgstore(msgstore)

        self.set_proptags(PropTags(self.get_fobj(), self.get_config()))
        self.reset_def_cols()

    ##
    ## Implementation of some abstract methods inherted from Folder
    ##

    def get_batch_size (self):
        return 100

    def prep_sync_lists (self, destid, sl, synct_sto=None, cnt=0):
        """See the documentation in folder.Folder"""

        logging.info('Querying MAPI for status of Contact Entries')

        ## Sort the DBIds so dest1 has the 'lower' ID
        dest1 = self.get_db().get_dbid()
        if dest1 > destid:
            dest2 = dest1
            dest1 = destid
        else:
            dest2 = destid

        ctable = self.get_contents()
        ## FIXME: This needs to be fixed. The ID will be different based on
        ## the actual remote database, of course.
        ctable.SetColumns((self.get_proptags().valu('ASYNK_PR_GCID'),
                           mapitags.PR_ENTRYID,
                           mapitags.PR_LAST_MODIFICATION_TIME),
                          0)

        i   = 0
        old = 0

        synct_str = self.get_config().get_last_sync_start(dest1, dest2)
        if not synct_sto:
            synct_sto = self.get_config().get_last_sync_stop(dest1, dest2)
        synct     = iso8601.parse(synct_sto)
        logging.debug('Last Start iso str : %s', synct_str)
        logging.debug('Last Stop  iso str : %s', synct_sto)
        logging.debug('Current Time       : %s', iso8601.tostring(time.time()))

        logging.info('Data obtained from MAPI. Processing...')

        while True:
            rows = ctable.QueryRows(1, 0)
            #if this is the last row then stop
            if len(rows) != 1:
                break

            (gid_tag, gid), (entryid_tag, entryid), (tt, modt) = rows[0]
            b64_entryid = base64.b64encode(entryid)

            sl.add_entry(b64_entryid, gid)

            if mapitags.PROP_TYPE(gid_tag) == mapitags.PT_ERROR:
                # Was not synced for whatever reason.
                sl.add_new(b64_entryid)
            else:
                if mapitags.PROP_TYPE(tt) == mapitags.PT_ERROR:
                    print 'Somethin wrong. no time stamp. i=', i
                else:
                    if utils.utc_time_to_local_ts(modt) <= synct:
                        old += 1
                    else:
                        sl.add_mod(b64_entryid, gid)

            i += 1
            if cnt != 0 and i >= cnt:
                break

        logging.debug('==== OL =====')
        logging.debug('num processed : %5d', i)
        logging.debug('num total     : %5d', len(sl.get_entries()))
        logging.debug('num new       : %5d', len(sl.get_news()))
        logging.debug('num mod       : %5d', len(sl.get_mods()))
        logging.debug('num old unmod : %5d', old)

        return (sl.get_news(), sl.get_mods(), sl.get_dels())

    def find_item (self, itemid):
        eid = base64.b64decode(itemid)

        print 'itemid : ', itemid

        olc = OLContact(self, eid=eid)
        return olc

    def find_items (self, iids):
        return [OLContact(self, eid=base64.b64decode(iid)) for iid in iids]

    def batch_create (self, sync_list, src_dbid, items):
        """See the documentation in folder.Folder"""

        my_dbid = self.get_dbid()
        c       = self.get_config()
        src_sync_tag = utils.get_sync_label_from_dbid(c, src_dbid)
        dst_sync_tag = utils.get_sync_label_from_dbid(c, my_dbid)

        for item in items:
            olc = OLContact(self, con=item)
            rid = item.get_itemid()
            olc.update_sync_tags(src_sync_tag, rid)

            ## FIXME: I strongly suspect this is not the most efficient way to
            ## do this. We should test by importing items in bulk into
            ## Outlook and measure performance, and fix this if needed.

            eid = olc.save()
            iid = olc.get_itemid()
            item.update_sync_tags(dst_sync_tag, iid)

    def batch_update (self, sync_list, src_dbid, items):
        """See the documentation in folder.Folder"""

        src_tag = utils.get_sync_label_from_dbid(self.get_config(), src_dbid)

        store = self.get_msgstore().get_obj()
        for item in items:
            olc = OLContact(self, con=item)

            ## We lose the sync tag as well when we blow everything. To ensure
            ## this gets recreated, put it back in.

            olc.update_sync_tags(src_tag, item.get_itemid())
            olprops = olc.get_olprops()
            oli     = olc.get_olitem()

            ## Wipe out the sucker
            try:
                def_cols = self.get_def_cols()
                hr, ps = oli.DeleteProps(def_cols)
            except Exception, e:
                logging.error('%s: Could not clear our MAPI props for: %s',
                              'gc:batch_update()', item.get_name())

            ## Now shove the new property set in
            try:
                hr, ps = oli.SetProps(olprops)
                oli.SaveChanges(mapi.KEEP_OPEN_READWRITE)
                logging.info('Successfully updated changes to Outlook for %s',
                             item.get_name())
            except Exception, e:
                logging.error('%s: Could not set new props set for: %s (%s)',
                              'gc:batch_update()', item.get_name(), e)

    def writeback_sync_tags (self, items):
        for item in items:
            item.save_sync_tags()

    def bulk_clear_sync_flags (self, dbids):
        """See the documentation in folder.Folder.

        Need to explore if there is a faster way than iterating through
        entries after a table lookup.
        """
        print dbids

        for dbid in dbids:
            tag = ''

            if dbid == 'gc':
                tag = 'ASYNK_PR_GCID'
            elif dbid == 'bb':
                tag = 'ASYNK_PR_BBID'
            else:
                continue

            print 'Processing tag: ', tag
            self._clear_tag(tag)

    def __str__ (self):
        if self.type == Folder.PR_IPM_CONTACT_ENTRYID:
            ret = 'Contacts'
        elif self.type == Folder.PR_IPM_NOTE_ENTRYID:
            ret = 'Notes'
        elif self.type == Folder.PR_IPM_TASK_ENTRYID:
            ret = 'Tasks'

        return ('%s.\tName: %s;\tEID: %s;\tStore: %s' % (
            ret, self.name, base64.b64encode(self.entryid),
            self.store.name))

    ##
    ## First some get_ and set_ routines
    ##

    ## Note: For Outlook related methods, itemid and entryid are aliases.

    def get_entryid (self):
        return self.get_itemid()

    def set_entryid (self, entryid):
        return self.set_itemid(entryid)

    def get_proptags (self):
        return self.proptags

    def set_proptags (self, p):
        self.proptags = p

    def reset_def_cols (self):
        self.def_cols  = (self.get_contents().QueryColumns(0) +
                          (self.get_proptags().valu('ASYNK_PR_GCID'),))

    def get_def_cols (self):
        return self.def_cols

    def get_fobj (self):
        return self._get_prop('fobj')

    def set_fobj (self, fobj):
        self._set_prop('fobj', fobj)

    def get_msgstore (self):
        return self._get_prop('msgstore')

    def set_msgstore (self, msgstore):
        self._set_prop('msgstore', msgstore)

    ##
    ## Now the more substantial Methods
    ##

    def get_contents (self):
        return self.get_fobj().GetContentsTable(mapi.MAPI_UNICODE)

    def del_entries (self, eids):
        """eids should be a list of EntryIDs - in binary format, as used by
        the MAPI routines."""

        num = len(eids)
        cf  = self.get_fobj()
        if num:
            logging.debug('Deleting %d entries in Outlook', num)
            hr = cf.DeleteMessages(eids, 0, None, 0)
            cf.SaveChanges(mapi.KEEP_OPEN_READWRITE)

    def _clear_tag (self, tag):
        logging.info('Querying MAPI for all data needed to clear flag')
        ctable = self.get_contents()
        ctable.SetColumns((self.get_proptags().valu(tag), mapitags.PR_ENTRYID), 0)
        logging.info('Data obtained from MAPI. Clearing one at a time')

        cnt = 0
        i   = 0
        store = self.get_msgstore().get_obj()
        hr = ctable.SeekRow(mapi.BOOKMARK_BEGINNING, 0)

        while True:
            rows = ctable.QueryRows(1, 0)
            # if this is the last row then stop
            if len(rows) != 1:
                break

            (gid_tag, gid), (entryid_tag, entryid) = rows[0]

            i += 1
            if mapitags.PROP_TYPE(gid_tag) != mapitags.PT_ERROR:
                entry = store.OpenEntry(entryid, None, mapi.MAPI_BEST_ACCESS)
                hr, ps = entry.DeleteProps([gid_tag])
                entry.SaveChanges(mapi.KEEP_OPEN_READWRITE)

                cnt += 1

        logging.info('Num entries cleared: %d. i = %d', cnt, i)
        return cnt

class OLContactsFolder(OLFolder):
    def __init__ (self, db, entryid, name, fobj, msgstore):
        OLFolder.__init__(self, db, entryid, name, fobj, msgstore)
        self.set_type(Folder.PR_IPM_CONTACT_ENTRYID)

        self.print_key_stats()

    def print_key_stats (self):
        print 'Contacts Folder Name: ', self.get_name()

class OLNotesFolder(OLFolder):
    def __init__ (self, db, entryid, name, fobj, msgstore):
        OLFolder.__init__(self, db, entryid, name, fobj, msgstore)
        self.set_type(Folder.PR_IPM_NOTE_ENTRYID)

class OLTasksFolder(OLFolder):
    def __init__ (self, db, entryid, name, fobj, msgstore):
        OLFolder.__init__(self, db, entryid, name, fobj, msgstore)
        self.set_type(Folder.PR_IPM_TASK_ENTRYID)

    def print_key_stats (self):
        total       = 0
        recurring   = 0
        expired     = 0
        completed   = 0

        ctable = self.get_obj().GetContentsTable(mapi.MAPI_UNICODE)
        ctable.SetColumns(self.def_cols, 0)

        while True:
            rows = ctable.QueryRows(1, 0)
            #if this is the last row then stop
            if len(rows) != 1:
                break

            total += 1

            props = dict(rows[0])

            try:
                entryid = props[mapitags.PR_ENTRYID]
            except AttributeError, e:
                entryid = 'Not Available'

            try:
                subject = props[mapitags.PR_SUBJECT_W]
            except AttributeError, e:
                subject = 'Not Available'

            try:
                complete = props[self.get_proptags().valu('ASYNK_PR_TASK_COMPLETE')]
                if complete:
                    completed += 1
            except KeyError, e:
                complete = 'Not Available'

            try:
                tag = self.get_proptags().valu('ASYNK_PR_TASK_RECUR')
                recurr_status = props[tag]
                if recurr_status:
                    recurring += 1
            except KeyError, e:
                recurr_status = 'Not Available'

            try:
                tag = self.get_proptags().valu('ASYNK_PR_TASK_STATE')
                state = props[tag]
            except KeyError, e:
                state = 'Not Available'

            try:
                tag = self.get_proptags().valu('ASYNK_PR_TASK_DUE_DATE')
                duedate = utils.pytime_to_yyyy_mm_dd(props[tag])
            except KeyError, e:
                duedate = 'Not Available'


            if complete:
                continue

            print 'Task #%3d: Heading: %s' % (total, subject)
            print '\tEntryID   : ', base64.b64encode(entryid)
            print '\tCompleted : ', complete
            print '\tRecurring : ', recurr_status
            print '\tState     : ', state
            print '\tDue Date  : ', duedate
            print '\n'

        print '===== Summary Status for Task Folder: %s ======' % self.name
        print '\tTotal Tasks count : %4d' % total
        print '\tRecurring count   : %4d' % recurring
        print '\tExpired count     : %4d' % expired
        print '\tCompleted count   : %4d' % completed

class OLAppointmentsFolder(OLFolder):
    def __init__ (self, db, entryid, name, fobj, msgstore):
        OLFolder.__init__(self, db, entryid, name, fobj, store)
        self.set_type(Folder.PR_IPM_APPOINTMENT_ENTRYID)

class PropTags:
    """This Singleton class represents a set of all the possible mapi property
    tags. In general the mapitags module has pretty usable constants
    defined. However MAPI compllicates things with 'Named Properties' - which
    are not static, but have to be generated at runtime (not sure what all
    parameters change it...). This class includes all the mapitags properties
    as well as a set of hand selected named properties that are relevant for
    us here."""

    PSETID_Address_GUID = '{00062004-0000-0000-C000-000000000046}'
    PSETID_Task_GUID    = '{00062003-0000-0000-c000-000000000046}'

    def __init__ (self, def_cf, config):
        self.name_hash = {}
        self.valu_hash = {}

        # We use the def_cf to lookup named properties. I suspect this will
        # have to be changed when we start supporting multiple profiles and
        # folders...
        self.def_cf = def_cf
        self.config = config

        # Load up all available properties from mapitags module

        for name, value in mapitags.__dict__.iteritems():
            if name[:3] == 'PR_':
                # Store both the full ID (including type) and just the ID.
                # This is so PR_FOO_A and PR_FOO_W are still
                # differentiated. Note that in the following call, the value
                # hash will only contain the full ID.
                self.put(name=name, value=mapitags.PROP_ID(value))
                self.put(name=name, value=value)

        # Now Add a bunch of named properties that we are specifically
        # interested in.

        self.put(name='ASYNK_PR_FILE_AS', value=self.get_file_as_prop_tag())

        self.put(name='ASYNK_PR_EMAIL_1', value=self.get_email_prop_tag(1))
        self.put(name='ASYNK_PR_EMAIL_2', value=self.get_email_prop_tag(2))
        self.put(name='ASYNK_PR_EMAIL_3', value=self.get_email_prop_tag(3))

        self.put(name='ASYNK_PR_IM_1', value=self.get_im_prop_tag(1))

        self.put(name='ASYNK_PR_GCID', value=self.get_gid_prop_tag('gc'))
        self.put(name='ASYNK_PR_BBID', value=self.get_gid_prop_tag('bb'))

        self.put('ASYNK_PR_TASK_DUE_DATE', self.get_task_due_date_tag())
        self.put('ASYNK_PR_TASK_STATE',    self.get_task_state_tag())
        self.put('ASYNK_PR_TASK_RECUR',    self.get_task_recur_tag())
        self.put('ASYNK_PR_TASK_COMPLETE', self.get_task_complete_tag())
        self.put('ASYNK_PR_TASK_DATE_COMPLETED',
                 self.get_task_date_completed_tag())

    def valu (self, name):
        return self.name_hash[name]

    def name (self, valu):
        return self.valu_hash[valu]

    ## The rest of the methods below are internal to the class.

    def put (self, name, value):
        self.name_hash[name]  = value
        self.valu_hash[value] = name

    # Routines to construct the property tags for named property. Intended to
    # be used only once in the constructor

    def get_email_prop_tag (self, n):
        """MAPI is crappy.

        Email addresses of the EX type do not conatain an SMTP address
        value for their PR_EMAIL_ADDRESS property tag. While the desired
        smtp address is present in the system the property tag that will
        help us fetch it is not a constant and will differ from system
        to system, and from PST file to PST file. The tag has to be
        dynamically generated.

        The routine jumps through the requisite hoops and appends those
        property tags to the supplied fields array. The augmented fields
        array is then returned.
        """
        if n <= 1:
            try:
                return self.valu('ASYNK_PR_EMAIL_1')
            except KeyError, e:
                prop_name = [(self.PSETID_Address_GUID, 0x8084)]
                prop_type = mapitags.PT_UNICODE
                prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)
                return (prop_type | prop_ids[0])

        prev_tag      = self.get_email_prop_tag(n-1)
        prev_tag_id   = mapitags.PROP_ID(prev_tag)
        prev_tag_type = mapitags.PROP_TYPE(prev_tag)

        return mapitags.PROP_TAG(prev_tag_type, prev_tag_id+1)

    def get_im_prop_tag (self, n):
        """I am no expert at this stuff but I found 4 InstantMessaging
        properties looking through the MAPI documentation. They are know by
        these "canonical property names": PidNameInstantMessagingAddress1,
        PidNameInstantMessagingAddress2, PidNameInstantMessagingAddress3 (all
        in the PSETID_AirSync property set) and PidLidInstantMessagingAddress
        that is a part of the PSETID_Address property set. In Outlook 2007
        documentation
        (http://msdn.microsoft.com/en-us/library/cc963764(v=office.12).aspx),
        the first three have been deprected.

        The long and short of all of that is that Outlook only supports a
        singlle instant messaging address, and we have only one property tag
        for that. Thank you."""

        plid = 0x00008062

        if n <= 1:
            try:
                return self.valu('ASYNK_PR_IM_1')
            except KeyError, e:
                prop_name = [(self.PSETID_Address_GUID, plid)]
                prop_type = mapitags.PT_UNICODE
                prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)
                return (prop_type | prop_ids[0])

        if n > 1:
            return None

    def get_gid_prop_tag (self, dbid):
        gid = self.config.get_olsync_gid(dbid)
        prop_name = [(self.config.get_olsync_guid(), gid)]
        prop_type = mapitags.PT_UNICODE
        prop_ids  = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_file_as_prop_tag (self):
        prop_name = [(self.PSETID_Address_GUID, 0x8005)]
        prop_type = mapitags.PT_UNICODE
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_task_due_date_tag (self):
        prop_name = [(self.PSETID_Task_GUID, 0x8105)]
        prop_type = mapitags.PT_SYSTIME
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_task_date_completed_tag (self):
        prop_name = [(self.PSETID_Task_GUID, 0x810f)]
        prop_type = mapitags.PT_SYSTIME
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_task_state_tag (self):
        prop_name = [(self.PSETID_Task_GUID, 0x8113)]
        prop_type = mapitags.PT_LONG
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_task_complete_tag (self):
        prop_name = [(self.PSETID_Task_GUID, 0x811c)]
        prop_type = mapitags.PT_BOOLEAN
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

    def get_task_recur_tag (self):
        prop_name = [(self.PSETID_Task_GUID, 0x8126)]
        prop_type = mapitags.PT_BOOLEAN
        prop_ids = self.def_cf.GetIDsFromNames(prop_name, mapi.MAPI_CREATE)

        return (prop_type | prop_ids[0])

def main (argv=None):

    from state import Config
    from pimdb_ol import OLPIMDB

    logging.debug('Getting started... Reading Config File...')
    config = Config('../app_state.json')

    ol     = OLPIMDB(config)

if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        main()
    except Exception, e:
        print 'Caught Exception... Hm. Need to cleanup.'
        print 'Full Exception as here:', traceback.format_exc()