########################################################################
# File name: service.py
# This file is part of: aioxmpp
#
# LICENSE
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program.  If not, see
# <http://www.gnu.org/licenses/>.
#
########################################################################
import asyncio

import aioxmpp
import aioxmpp.callbacks as callbacks
import aioxmpp.service as service
import aioxmpp.disco as disco
import aioxmpp.pubsub as pubsub
import aioxmpp.private_xml as private_xml

from . import xso as bookmark_xso


# TODO: use private storage in pubsub where available.
# TODO: sync bookmarks between pubsub and private xml storage
class BookmarkClient(service.Service):
    """
    Supports retrieval and storage of bookmarks on the server.
    It currently only supports :xep:`Private XML Storage <49>` as
    backend.

    .. automethod:: get_bookmarks

    .. automethod:: set_bookmarks

    .. note:: The bookmark protocol is prone to race conditions if
              several clients access it concurrently. Be careful to
              use a get-modify-set pattern.

    .. note:: Some other clients extend the bookmark format. For now
              those extensions are silently dropped by our XSOs, and
              therefore are lost, when changing the bookmarks with
              aioxmpp. This is considered a bug to be fixed in the future.
    """

    ORDER_AFTER = [
        private_xml.PrivateXMLService,
    ]

    on_bookmark_added = callbacks.Signal()
    on_bookmark_removed = callbacks.Signal()
    on_bookmark_changed = callbacks.Signal()

    def __init__(self, client, **kwargs):
        super().__init__(client, **kwargs)
        self._private_xml = self.dependencies[private_xml.PrivateXMLService]
        self._bookmark_cache = []

    @asyncio.coroutine
    def _get_bookmarks(self):
        """
        Get the stored bookmarks from the server.

        :returns: the bookmarks as a :class:`~bookmark_xso.Storage` object
        """
        res = yield from self._private_xml.get_private_xml(
            bookmark_xso.Storage()
        )

        return res.bookmarks

    @asyncio.coroutine
    def _set_bookmarks(self, bookmarks):
        """
        Set the bookmarks stored on the server.
        """
        storage = bookmark_xso.Storage()
        storage.bookmarks[:] = bookmarks
        yield from self._private_xml.set_private_xml(storage)

    def _diff_emit_update(self, new_bookmarks):
        """
        Diff the bookmark cache and the new bookmark state, emit signals as
        needed and set the bookmark cache to the new data.
        """

        self.logger.debug("diffing %s, %s", self._bookmark_cache, new_bookmarks)

        def subdivide(level, old, new):

            if len(old) == len(new) == 1:
                old_entry = old.pop()
                new_entry = new.pop()
                if old_entry == new_entry:
                    pass
                else:
                    self.on_bookmark_changed(old_entry, new_entry)
                return ([], [])

            elif len(old) == 0:
                return ([], new)

            elif len(new) == 0:
                return (old, [])

            else:
                try:
                    groups = {}
                    for entry in old:
                        group = groups.setdefault(
                            entry.secondary[level],
                            ([], [])
                        )
                        group[0].append(entry)

                    for entry in new:
                        group = groups.setdefault(
                            entry.secondary[level],
                            ([], [])
                        )
                        group[1].append(entry)
                except IndexError:
                    # the classification is exhausted, this means
                    # all entries in this bin are equal by the
                    # defininition of bookmark equivalence!
                    common = min(len(old), len(new))
                    assert old[:common] == new[:common]
                    return (old[common:], new[common:])

                old_unhandled, new_unhandled = [], []
                for old, new in groups.values():
                    unhandled = subdivide(level+1, old, new)
                    old_unhandled += unhandled[0]
                    new_unhandled += unhandled[1]

                # match up unhandleds as changes as early as possible
                i = -1
                for i, (old_entry, new_entry) in enumerate(
                        zip(old_unhandled, new_unhandled)):
                    self.logger.debug("changed %s -> %s", old_entry, new_entry)
                    self.on_bookmark_changed(old_entry, new_entry)
                i += 1
                return old_unhandled[i:], new_unhandled[i:]

        # group the bookmarks into groups whose elements may transform
        # among one another by on_bookmark_changed events. This information
        # is given by the type of the bookmark and the .primary property
        changable_groups = {}

        for item in self._bookmark_cache:
            group = changable_groups.setdefault(
                (type(item), item.primary),
                ([], [])
            )
            group[0].append(item)

        for item in new_bookmarks:
            group = changable_groups.setdefault(
                (type(item), item.primary),
                ([], [])
            )
            group[1].append(item)

        for old, new in changable_groups.values():

            # the first branches are fast paths which should catch
            # most cases – especially all cases where each bare jid of
            # a conference bookmark or each url of an url bookmark is
            # only used in one bookmark
            if len(old) == len(new) == 1:
                old_entry = old.pop()
                new_entry = new.pop()
                if old_entry == new_entry:
                    # the bookmark is unchanged, do not emit an event
                    pass
                else:
                    self.logger.debug("changed %s -> %s", old_entry, new_entry)
                    self.on_bookmark_changed(old_entry, new_entry)
            elif len(new) == 0:
                for removed in old:
                    self.logger.debug("removed %s", removed)
                    self.on_bookmark_removed(removed)
            elif len(old) == 0:
                for added in new:
                    self.logger.debug("added %s", added)
                    self.on_bookmark_added(added)
            else:
                old, new = subdivide(0, old, new)

                assert len(old) == 0 or len(new) == 0

                for removed in old:
                    self.logger.debug("removed %s", removed)
                    self.on_bookmark_removed(removed)

                for added in new:
                    self.logger.debug("added %s", added)
                    self.on_bookmark_added(added)

        self._bookmark_cache = new_bookmarks

    @asyncio.coroutine
    def get_bookmarks(self):
        """
        Get the stored bookmarks from the server and emit signals.

        :returns: the bookmarks as a :class:`~bookmark_xso.Storage` object
        """
        bookmarks = yield from self._get_bookmarks()
        self._diff_emit_update(bookmarks)
        return bookmarks

    @asyncio.coroutine
    def set_bookmarks(self, bookmarks):
        """
        Set the stored bookmarks.
        """
        yield from self._set_bookmarks(bookmarks)
        self._diff_emit_update(bookmarks)
        return bookmarks

    @asyncio.coroutine
    def sync(self):
        """
        Sync the bookmarks between the local representation and the
        server.

        This must be called periodically to assure that change events
        are emitted.
        """
        yield from self.get_bookmarks()

    @asyncio.coroutine
    def add_bookmark(self, new_bookmark):
        """
        Add a bookmark.

        Already existant bookmarks are not added twice.
        """
        bookmarks = yield from self._get_bookmarks()
        for bookmark in bookmarks:
            if bookmark == new_bookmark:
                break
        else:
            bookmarks.append(new_bookmark)
        yield from self._set_bookmarks(bookmarks)

        self._diff_emit_update(bookmarks)

    @asyncio.coroutine
    def remove_bookmark(self, bookmark_to_remove):
        """
        Remove a bookmark.

        This does nothing if the bookmarks does not match an existing
        bookmark according to bookmark-equality.
        """
        bookmarks = yield from self._get_bookmarks()
        result = []
        not_removed = True
        for bookmark in bookmarks:
            if not_removed and bookmark == bookmark_to_remove:
                not_removed = False
                continue
            else:
                result.append(bookmark)
        yield from self._set_bookmarks(result)
        self._diff_emit_update(result)

    @asyncio.coroutine
    def update_bookmark(self, old, new):
        """
        Update a bookmark.

        The bookmark matches an existing bookmark `old` according to
        bookmark equalitiy and replaces it by `new`. The bookmark is
        added if no bookmark matching `old` exists.
        """
        bookmarks = yield from self._get_bookmarks()
        for i, bookmark in enumerate(bookmarks):
            if bookmark == old:
                bookmarks[i] = new
                break
        else:
            bookmarks.append(new)
        yield from self._set_bookmarks(bookmarks)

        self._diff_emit_update(bookmarks)
