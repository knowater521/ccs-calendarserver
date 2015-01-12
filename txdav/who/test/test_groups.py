##
# Copyright (c) 2013-2015 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Group membership caching implementation tests
"""

from twext.enterprise.jobqueue import JobItem
from twext.who.idirectory import RecordType
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twistedcaldav.stdconfig import config
from twistedcaldav.test.util import StoreTestCase
from txdav.common.icommondatastore import NotFoundError
from txdav.who.groups import GroupCacher, diffAssignments, GroupRefreshWork
from txdav.who.test.support import TestRecord, CalendarInMemoryDirectoryService



class GroupCacherTest(StoreTestCase):

    @inlineCallbacks
    def setUp(self):
        yield super(GroupCacherTest, self).setUp()
        self.groupCacher = GroupCacher(self.directory)


    @inlineCallbacks
    def test_multipleCalls(self):
        """
        Ensure multiple calls to groupByUID() don't raise an exception
        """

        store = self.storeUnderTest()
        txn = store.newTransaction()

        record = yield self.directory.recordWithUID(u"__top_group_1__")
        yield txn.groupByUID(record.uid)
        yield txn.groupByUID(record.uid)

        yield txn.commit()


    @inlineCallbacks
    def test_refreshGroup(self):
        """
        Verify refreshGroup() adds a group to the Groups table with the
        expected membership hash value and members
        """

        store = self.storeUnderTest()
        txn = store.newTransaction()

        record = yield self.directory.recordWithUID(u"__top_group_1__")
        yield self.groupCacher.refreshGroup(txn, record.uid)

        (
            groupID, _ignore_name, membershipHash, _ignore_modified,
            extant
        ) = (yield txn.groupByUID(record.uid))

        self.assertEquals(extant, True)
        self.assertEquals(membershipHash, "553eb54e3bbb26582198ee04541dbee4")

        groupUID, name, membershipHash, extant = (yield txn.groupByID(groupID))
        self.assertEquals(groupUID, record.uid)
        self.assertEquals(name, u"Top Group 1")
        self.assertEquals(membershipHash, "553eb54e3bbb26582198ee04541dbee4")
        self.assertEquals(extant, True)

        members = (yield txn.groupMemberUIDs(groupID))
        self.assertEquals(
            set([u'__cdaboo1__', u'__glyph1__', u'__sagen1__', u'__wsanchez1__']),
            members
        )

        records = (yield self.groupCacher.cachedMembers(txn, groupID))
        self.assertEquals(
            set([r.uid for r in records]),
            set([u'__cdaboo1__', u'__glyph1__', u'__sagen1__', u'__wsanchez1__'])
        )

        # sagen is in the top group, even though it's actually one level
        # removed
        record = yield self.directory.recordWithUID(u"__sagen1__")
        groups = (yield self.groupCacher.cachedGroupsFor(txn, record.uid))
        self.assertEquals(set([u"__top_group_1__"]), groups)

        yield txn.commit()


    @inlineCallbacks
    def test_synchronizeMembers(self):
        """
        After loading in a group via refreshGroup(), pass new member sets to
        synchronizeMembers() and verify members are added and removed as
        expected
        """

        store = self.storeUnderTest()
        txn = store.newTransaction()

        # Refresh the group so it's assigned a group_id
        uid = u"__top_group_1__"
        yield self.groupCacher.refreshGroup(txn, uid)
        (
            groupID, name, _ignore_membershipHash, _ignore_modified,
            _ignore_extant
        ) = yield txn.groupByUID(uid)

        # Remove two members, and add one member
        newSet = set()
        for name in (u"wsanchez1", u"cdaboo1", u"dre1"):
            record = (
                yield self.directory.recordWithShortName(
                    RecordType.user,
                    name
                )
            )
            newSet.add(record.uid)
        added, removed = (
            yield self.groupCacher.synchronizeMembers(
                txn, groupID, newSet
            )
        )
        self.assertEquals(added, set(["__dre1__", ]))
        self.assertEquals(removed, set(["__glyph1__", "__sagen1__", ]))
        records = (yield self.groupCacher.cachedMembers(txn, groupID))
        self.assertEquals(
            set([r.shortNames[0] for r in records]),
            set(["wsanchez1", "cdaboo1", "dre1"])
        )

        # Remove all members
        added, removed = (
            yield self.groupCacher.synchronizeMembers(txn, groupID, set())
        )
        self.assertEquals(added, set())
        self.assertEquals(removed, set(["__wsanchez1__", "__cdaboo1__", "__dre1__", ]))
        records = (yield self.groupCacher.cachedMembers(txn, groupID))
        self.assertEquals(len(records), 0)

        yield txn.commit()


    @inlineCallbacks
    def test_groupByID(self):

        store = self.storeUnderTest()
        txn = store.newTransaction()

        # Non-existent groupID
        yield self.failUnlessFailure(txn.groupByID(42), NotFoundError)

        uid = u"__top_group_1__"
        hash = "553eb54e3bbb26582198ee04541dbee4"
        yield self.groupCacher.refreshGroup(txn, uid)
        (
            groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
            _ignore_extant
        ) = yield txn.groupByUID(uid)
        results = yield txn.groupByID(groupID)
        self.assertEquals((uid, u"Top Group 1", hash, True), results)

        yield txn.commit()


    @inlineCallbacks
    def test_externalAssignments(self):

        store = self.storeUnderTest()
        txn = store.newTransaction()

        oldExternalAssignments = (yield txn.externalDelegates())
        self.assertEquals(oldExternalAssignments, {})

        newAssignments = {
            u"__wsanchez1__": (None, u"__top_group_1__")
        }
        yield self.groupCacher.scheduleExternalAssignments(
            txn, newAssignments, immediately=True
        )
        oldExternalAssignments = (yield txn.externalDelegates())
        self.assertEquals(
            oldExternalAssignments,
            {
                u"__wsanchez1__":
                (
                    None,
                    u"__top_group_1__"
                )
            }
        )

        newAssignments = {
            u"__cdaboo1__":
            (
                u"__sub_group_1__",
                None
            ),
            u"__wsanchez1__":
            (
                u"__sub_group_1__",
                u"__top_group_1__"
            ),
        }

        yield self.groupCacher.scheduleExternalAssignments(
            txn, newAssignments, immediately=True
        )
        oldExternalAssignments = (yield txn.externalDelegates())
        self.assertEquals(
            oldExternalAssignments,
            {
                u"__wsanchez1__":
                (
                    u"__sub_group_1__",
                    u"__top_group_1__"
                ),
                u"__cdaboo1__":
                (
                    u"__sub_group_1__",
                    None
                )
            }
        )

        allGroupDelegates = (yield txn.allGroupDelegates())
        self.assertEquals(
            allGroupDelegates,
            set(
                [
                    u"__top_group_1__",
                    u"__sub_group_1__"
                ]
            )
        )

        # Fault in the read-only group
        yield self.groupCacher.refreshGroup(txn, u"__sub_group_1__")

        # Wilfredo should have Sagen and Daboo as read-only delegates
        delegates = (yield txn.delegates(
            u"__wsanchez1__", False, expanded=True)
        )
        self.assertEquals(
            delegates,
            set(
                [
                    u"__sagen1__",
                    u"__cdaboo1__"
                ]
            )
        )

        # Fault in the read-write group
        yield self.groupCacher.refreshGroup(txn, u"__top_group_1__")

        # Wilfredo should have 4 users as read-write delegates
        delegates = (yield txn.delegates(
            u"__wsanchez1__", True, expanded=True)
        )
        self.assertEquals(
            delegates,
            set(
                [
                    u"__sagen1__",
                    u"__cdaboo1__",
                    u"__glyph1__"
                ]
            )
        )

        #
        # Now, remove some external assignments
        #
        newAssignments = {
            u"__wsanchez1__":
            (
                u"__sub_group_1__",
                None
            ),
        }
        yield self.groupCacher.scheduleExternalAssignments(
            txn, newAssignments, immediately=True
        )
        oldExternalAssignments = (yield txn.externalDelegates())
        self.assertEquals(
            oldExternalAssignments,
            {
                u"__wsanchez1__":
                (
                    u"__sub_group_1__",
                    None
                ),
            }
        )

        allGroupDelegates = (yield txn.allGroupDelegates())
        self.assertEquals(
            allGroupDelegates,
            set(
                [
                    u"__sub_group_1__"
                ]
            )
        )

        # Wilfredo should have Sagen and Daboo as read-only delegates
        delegates = (yield txn.delegates(
            u"__wsanchez1__", False, expanded=True)
        )
        self.assertEquals(
            delegates,
            set(
                [
                    u"__sagen1__",
                    u"__cdaboo1__"
                ]
            )
        )

        # Wilfredo should have no read-write delegates
        delegates = (yield txn.delegates(
            u"__wsanchez1__", True, expanded=True)
        )
        self.assertEquals(
            delegates,
            set([])
        )

        # Only 1 group as delegate now:
        allGroupDelegates = (yield txn.allGroupDelegates())
        self.assertEquals(
            allGroupDelegates,
            set(
                [
                    u"__sub_group_1__"
                ]
            )
        )

        #
        # Say somebody messed up and stuck a non-existent group UID in
        # as a delegate
        #
        newAssignments = {
            u"__wsanchez1__":
            (
                u"__sub_group_1__",
                u"__non_existent_group__",
            ),
        }
        yield self.groupCacher.scheduleExternalAssignments(
            txn, newAssignments, immediately=True
        )
        oldExternalAssignments = (yield txn.externalDelegates())
        self.assertEquals(
            oldExternalAssignments,
            {
                u"__wsanchez1__":
                (
                    u"__sub_group_1__",
                    None  # <--- (not __non_existent_group__)
                ),
            }
        )

        yield txn.commit()


    def test_diffAssignments(self):
        """
        Ensure external proxy assignment diffing works
        """

        self.assertEquals(
            (
                # changed
                [],
                # removed
                [],
            ),
            diffAssignments(
                # old
                {},
                # new
                {}
            )
        )

        self.assertEquals(
            (
                # changed
                [],
                # removed
                [],
            ),
            diffAssignments(
                # old
                {"B": ("1", "2")},
                # new
                {"B": ("1", "2")},
            )
        )

        self.assertEquals(
            (
                # changed
                [("A", ("1", "2")), ("B", ("3", "4"))],
                # removed
                [],
            ),
            diffAssignments(
                # old
                {},
                # new
                {"A": ("1", "2"), "B": ("3", "4")}
            )
        )

        self.assertEquals(
            (
                # changed
                [],
                # removed
                ["A", "B"],
            ),
            diffAssignments(
                # old
                {"A": ("1", "2"), "B": ("3", "4")},
                # new
                {},
            )
        )

        self.assertEquals(
            (
                # changed
                [('C', ('4', '5')), ('D', ('7', '8'))],
                # removed
                ["B"],
            ),
            diffAssignments(
                # old
                {"A": ("1", "2"), "B": ("3", "4"), "C": ("5", "6")},
                # new
                {"D": ("7", "8"), "C": ("4", "5"), "A": ("1", "2")},
            )
        )


    @inlineCallbacks
    def test_recursiveGroup(self):
        """
        Verify refreshGroup() adds a group to the Groups table with the
        expected membership hash value and members
        """

        store = self.storeUnderTest()
        txn = store.newTransaction()

        record = yield self.directory.recordWithUID(u"recursive1_coasts")
        members = yield record.expandedMembers()
        self.assertEquals(
            set([r.uid for r in members]),
            set([u'6423F94A-6B76-4A3A-815B-D52CFD77935D', u'5A985493-EE2C-4665-94CF-4DFEA3A89500'])
        )

        yield txn.commit()


    @inlineCallbacks
    def test_groupChangeCacheNotification(self):
        """
        Verify refreshGroup() triggers a cache notification for the group and all
        members that are added or removed
        """

        class TestNotifier(object):
            changedTokens = []
            def changed(self, token):
                self.changedTokens.append(token)

        self.groupCacher.cacheNotifier = TestNotifier()

        # No change
        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__sub_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [])

        # Add member to group
        record = yield self.directory.recordWithUID(u"__top_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__dre1__")
        yield record.addMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        # Remove member from group
        record = yield self.directory.recordWithUID(u"__top_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__dre1__")
        yield record.removeMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        # Add member to sub-group
        record = yield self.directory.recordWithUID(u"__sub_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__dre1__")
        yield record.addMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__sub_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__sub_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        # Remove member from sub-group
        record = yield self.directory.recordWithUID(u"__sub_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__dre1__")
        yield record.removeMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__sub_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__sub_group_1__",
            "__dre1__",
        ])
        TestNotifier.changedTokens = []

        # Remove sub-group member from group
        record = yield self.directory.recordWithUID(u"__top_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__sub_group_1__")
        yield record.removeMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__sagen1__",
            "__cdaboo1__",
        ])
        TestNotifier.changedTokens = []

        # Add sub-group member to group
        record = yield self.directory.recordWithUID(u"__top_group_1__")
        addrecord = yield self.directory.recordWithUID(u"__sub_group_1__")
        yield record.addMembers([addrecord, ])

        yield self.groupCacher.refreshGroup(self.transactionUnderTest(), "__top_group_1__")
        yield self.commit()

        self.assertEqual(TestNotifier.changedTokens, [
            "__top_group_1__",
            "__sagen1__",
            "__cdaboo1__",
        ])
        TestNotifier.changedTokens = []



class DynamicGroupTest(StoreTestCase):


    @inlineCallbacks
    def setUp(self):
        yield super(DynamicGroupTest, self).setUp()

        self.directory = CalendarInMemoryDirectoryService(None)
        self.store.setDirectoryService(self.directory)
        self.groupCacher = GroupCacher(self.directory)

        self.numUsers = 100

        # Add users
        records = []
        fieldName = self.directory.fieldName
        for i in xrange(self.numUsers):
            records.append(
                TestRecord(
                    self.directory,
                    {
                        fieldName.uid: u"foo{ctr:05d}".format(ctr=i),
                        fieldName.shortNames: (u"foo{ctr:05d}".format(ctr=i),),
                        fieldName.fullNames: (u"foo{ctr:05d}".format(ctr=i),),
                        fieldName.recordType: RecordType.user,
                    }
                )
            )

        # Add two groups
        for uid in (u"testgroup", u"emptygroup",):
            records.append(
                TestRecord(
                    self.directory,
                    {
                        fieldName.uid: uid,
                        fieldName.recordType: RecordType.group,
                    }
                )
            )

            yield self.directory.updateRecords(records, create=True)

        # add members to test group
        group = yield self.directory.recordWithUID(u"testgroup")
        members = yield self.directory.recordsWithRecordType(RecordType.user)
        yield group.setMembers(members)


        def doWork(self):
            self.transaction._groupCacher = groupCacher
            return unpatchedDoWork(self)

        groupCacher = self.groupCacher
        unpatchedDoWork = GroupRefreshWork.doWork
        self.patch(GroupRefreshWork, "doWork", doWork)

        config.AutomaticPurging.Enabled = True


    @inlineCallbacks
    def test_extant(self):
        """
        Verify that once a group is removed from the directory, the next call
        to refreshGroup() will set the "extent" to False.  Add the group back
        to the directory and "extent" becomes True.
        """
        store = self.storeUnderTest()

        for uid in (u"testgroup", u"emptygroup",):

            txn = store.newTransaction()
            yield self.groupCacher.refreshGroup(txn, uid)
            (
                _ignore_groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = (yield txn.groupByUID(uid))
            yield txn.commit()

            self.assertTrue(extant)

            # Remove the group
            yield self.directory.removeRecords([uid])

            txn = store.newTransaction()
            yield self.groupCacher.refreshGroup(txn, uid)
            (
                groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = (yield txn.groupByUID(uid))
            yield txn.commit()

            # Extant = False
            self.assertFalse(extant)

            # The list of members stored in the DB for this group is now empty
            txn = store.newTransaction()
            members = yield txn.groupMemberUIDs(groupID)
            yield txn.commit()
            self.assertEquals(members, set())

            # Add the group back into the directory
            fieldName = self.directory.fieldName
            yield self.directory.updateRecords(
                (
                    TestRecord(
                        self.directory,
                        {
                            fieldName.uid: uid,
                            fieldName.recordType: RecordType.group,
                        }
                    ),
                ),
                create=True
            )
            if uid == u"testgroup":
                group = yield self.directory.recordWithUID(uid)
                members = yield self.directory.recordsWithRecordType(RecordType.user)
                yield group.setMembers(members)

            txn = store.newTransaction()
            yield self.groupCacher.refreshGroup(txn, uid)
            (
                groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = (yield txn.groupByUID(uid))
            yield txn.commit()

            # Extant = True
            self.assertTrue(extant)

            # The list of members stored in the DB for this group has 100 users
            txn = store.newTransaction()
            members = yield txn.groupMemberUIDs(groupID)
            yield txn.commit()
            self.assertEquals(len(members), 100 if uid == u"testgroup" else 0)


    @inlineCallbacks
    def test_update_delete_unused(self):
        """
        Verify that unused groups are deleted from group cache
        """
        store = self.storeUnderTest()

        # unused group deleted
        for uid in (u"testgroup", u"emptygroup",):

            txn = store.newTransaction()
            yield self.groupCacher.refreshGroup(txn, uid)
            groupID = (yield txn.groupByUID(uid, create=False))[0]
            yield txn.commit()

            self.assertNotEqual(groupID, None)

            txn = store.newTransaction()
            yield self.groupCacher.update(txn)
            groupID = (yield txn.groupByUID(uid, create=False))[0]
            yield txn.commit()

            self.assertEqual(groupID, None)

        # delegate groups not deleted
        for uid in (u"testgroup", u"emptygroup",):

            txn = store.newTransaction()
            groupID = (yield txn.groupByUID(uid))[0]
            yield txn.addDelegateGroup(delegator=u"sagen", delegateGroupID=groupID, readWrite=True)
            yield txn.commit()

            self.assertNotEqual(groupID, None)

            txn = store.newTransaction()
            yield self.groupCacher.update(txn)
            yield txn.commit()
            yield JobItem.waitEmpty(store.newTransaction, reactor, 60)

            txn = store.newTransaction()
            groupID = (yield txn.groupByUID(uid, create=False))[0]
            yield txn.commit()

            self.assertNotEqual(groupID, None)

        # delegate group is deleted. unused group is deleted
        txn = store.newTransaction()
        testGroupID = (yield txn.groupByUID(u"testgroup", create=False))[0]
        yield txn.removeDelegateGroup(delegator=u"sagen", delegateGroupID=testGroupID, readWrite=True)
        testGroupID = (yield txn.groupByUID(u"testgroup", create=False))[0]
        emptyGroupID = (yield txn.groupByUID(u"emptygroup", create=False))[0]
        yield txn.commit()

        self.assertNotEqual(testGroupID, None)
        self.assertNotEqual(emptyGroupID, None)

        txn = store.newTransaction()
        yield self.groupCacher.update(txn)
        yield txn.commit()
        yield JobItem.waitEmpty(store.newTransaction, reactor, 60)

        txn = store.newTransaction()
        testGroupID = (yield txn.groupByUID(u"testgroup", create=False))[0]
        emptyGroupID = (yield txn.groupByUID(u"emptygroup", create=False))[0]
        yield txn.commit()

        self.assertEqual(testGroupID, None)
        self.assertNotEqual(emptyGroupID, None)


    @inlineCallbacks
    def test_update_delete_old_nonextant(self):
        """
        Verify that old missing groups are deleted from group cache
        """

        oldGroupPurgeIntervalSeconds = config.AutomaticPurging.GroupPurgeIntervalSeconds
        store = self.storeUnderTest()

        for uid in (u"testgroup", u"emptygroup",):

            config.AutomaticPurging.GroupPurgeIntervalSeconds = oldGroupPurgeIntervalSeconds
            txn = store.newTransaction()
            groupID = (yield txn.groupByUID(uid))[0]
            yield txn.addDelegateGroup(delegator=u"sagen", delegateGroupID=groupID, readWrite=True)
            (
                groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = yield txn.groupByUID(uid, create=False)
            yield txn.commit()

            self.assertTrue(extant)
            self.assertNotEqual(groupID, None)

            # Remove the group, still cached
            yield self.directory.removeRecords([uid])
            txn = store.newTransaction()
            yield self.groupCacher.update(txn)
            (
                groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = yield txn.groupByUID(uid, create=False)
            yield txn.commit()
            yield JobItem.waitEmpty(store.newTransaction, reactor, 60)

            txn = store.newTransaction()
            (
                groupID, _ignore_name, _ignore_membershipHash, _ignore_modified,
                extant
            ) = yield txn.groupByUID(uid, create=False)
            yield txn.commit()
            self.assertNotEqual(groupID, None)
            self.assertFalse(extant)

            # delete the group
            config.AutomaticPurging.GroupPurgeIntervalSeconds = "0.0"

            txn = store.newTransaction()
            yield self.groupCacher.update(txn)
            groupID = (yield txn.groupByUID(uid, create=False))[0]
            yield txn.commit()
            self.assertEqual(groupID, None)
