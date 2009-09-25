# Copyright 2009 10gen, Inc.
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

"""Cursor class to iterate over Mongo query results."""

import types
import struct
import warnings

import pymongo
import bson
from son import SON
from code import Code
from errors import InvalidOperation, OperationFailure, AutoReconnect

_QUERY_OPTIONS = {
    "tailable_cursor": 2,
    "slave_okay": 4,
    "oplog_replay": 8,
    "no_timeout": 16
}


class Cursor(object):
    """A cursor / iterator over Mongo query results.
    """

    def __init__(self, collection, spec, fields, skip, limit, slave_okay,
                 timeout, snapshot=False, _sock=None, _must_use_master=False):
        """Create a new cursor.

        Should not be called directly by application developers.
        """
        self.__collection = collection
        self.__spec = spec
        self.__fields = fields
        self.__skip = skip
        self.__limit = limit
        self.__slave_okay = slave_okay
        self.__timeout = timeout
        self.__snapshot = snapshot
        self.__ordering = None
        self.__explain = False
        self.__hint = None
        self.__socket = _sock
        self.__must_use_master = _must_use_master

        self.__data = []
        self.__id = None
        self.__connection_id = None
        self.__retrieved = 0
        self.__killed = False
        self.__left_bound = 0
        self.__right_bound = 100000

    def __del__(self):
        if self.__id and not self.__killed:
            self.__die()

    def rewind(self):
        """Rewind this cursor to it's unevaluated state.

        Reset this cursor if it has been partially or completely evaluated.
        Any options that are present on the cursor will remain in effect.
        Future iterating performed on this cursor will cause new queries to
        be sent to the server, even if the resultant data has already been
        retrieved by this cursor.
        """
        self.__data = []
        self.__id = None
        self.__connection_id = None
        self.__retrieved = 0
        self.__killed = False

        return self

    def clone(self):
        """Get a clone of this cursor.

        Returns a new Cursor instance with options matching those that have
        been set on the current instance. The clone will be completely
        unevaluated, even if the current instance has been partially or
        completely evaluated.
        """
        copy = Cursor(self.__collection, self.__spec, self.__fields,
                      self.__skip, self.__limit, self.__slave_okay,
                      self.__timeout, self.__snapshot)
        copy.__ordering = self.__ordering
        copy.__explain = self.__explain
        copy.__hint = self.__hint
        copy.__socket = self.__socket
        return copy

    def __die(self):
        """Closes this cursor.
        """
        if self.__id and not self.__killed:
            connection = self.__collection.database().connection()
            if self.__connection_id is not None:
                connection.close_cursor(self.__id, self.__connection_id)
            else:
                connection.close_cursor(self.__id)
        self.__killed = True

    def __query_spec(self):
        """Get the spec to use for a query.

        Just `self.__spec`, unless this cursor needs special query fields, like
        orderby.
        """
        if not self.__ordering and not self.__explain and not self.__hint:
            return self.__spec

        spec = SON({"query": self.__spec})
        if self.__ordering:
            spec["orderby"] = self.__ordering
        if self.__explain:
            spec["$explain"] = True
        if self.__hint:
            spec["$hint"] = self.__hint
        if self.__snapshot:
            spec["$snapshot"] = True
        return spec

    def __query_options(self):
        """Get the 4 byte query options string to use for this query.
        """
        options = 0
        if self.__slave_okay:
            options |= _QUERY_OPTIONS["slave_okay"]
        if not self.__timeout:
            options |= _QUERY_OPTIONS["no_timeout"]
        return struct.pack("<I", options)

    def __check_okay_to_chain(self):
        """Check if it is okay to chain more options onto this cursor.
        """
        if self.__retrieved or self.__id is not None:
            raise InvalidOperation("cannot set options after executing query")

    def limit(self, limit):
        """Limits the number of results to be returned by this cursor.

        Raises TypeError if limit is not an instance of int. Raises
        InvalidOperation if this cursor has already been used. The last `limit`
        applied to this cursor takes precedence.

        :Parameters:
          - `limit`: the number of results to return
        """
        if not isinstance(limit, types.IntType):
            raise TypeError("limit must be an int")
        self.__check_okay_to_chain()

        self.__limit = limit
        return self

    def skip(self, skip):
        """Skips the first `skip` results of this cursor.

        Raises TypeError if skip is not an instance of int. Raises
        InvalidOperation if this cursor has already been used. The last `skip`
        applied to this cursor takes precedence.

        :Parameters:
          - `skip`: the number of results to skip
        """
        if not isinstance(skip, types.IntType):
            raise TypeError("skip must be an int")
        self.__check_okay_to_chain()

        self.__skip = skip
        return self

    def __getitem__(self, index):
        """Applies skip and limit, when cursor[2:10] notation is used."""
        self.__check_okay_to_chain()

        if isinstance(index, slice):
            if index.stop is not None:
                self.__right_bound = min(self.__right_bound, self.__left_bound + index.stop)

            if index.start is not None:
                self.__left_bound += index.start

            self.skip(self.__left_bound)
            self.limit(self.__right_bound - self.__left_bound)
            return self

        clone = self.clone()
        clone.skip(index)
        clone.limit(1)
        obj = list(clone)
        if len(obj) != 1:
            raise IndexError
        return obj[0]


    def sort(self, key_or_list, direction=None):
        """Sorts this cursor's results.

        Takes either a single key and a direction, or a list of (key,
        direction) pairs. The key(s) must be an instance of (str, unicode), and
        the direction(s) must be one of (`pymongo.ASCENDING`,
        `pymongo.DESCENDING`). Raises InvalidOperation if this cursor has
        already been used. Only the last `sort` applied to this cursor has any
        effect.

        :Parameters:
          - `key_or_list`: a single key or a list of (key, direction) pairs
            specifying the keys to sort on
          - `direction` (optional): only used if key_or_list is a single
            key, if not given ASCENDING is assumed
        """
        self.__check_okay_to_chain()
        keys = pymongo._index_list(key_or_list, direction)
        self.__ordering = pymongo._index_document(keys)
        return self

    def count(self):
        """Get the size of the results set for this query.

        Returns the number of objects in the results set for this query. Does
        not take limit and skip into account. Raises OperationFailure on a
        database error.
        """
        command = SON([("count", self.__collection.name()),
                       ("query", self.__spec),
                       ("fields", self.__fields)])
        response = self.__collection.database()._command(command,
                                                         ["ns missing"])
        if response.get("errmsg", "") == "ns missing":
            return 0
        return int(response["n"])

    def __len__(self):
        total = self.count()
        return min(total, self.__limit or total)

    def explain(self):
        """Returns an explain plan record for this cursor.
        """
        c = self.clone()
        c.__explain = True

        # always use a hard limit for explains
        if c.__limit:
            c.__limit = -abs(c.__limit)
        return c.next()

    def hint(self, index):
        """Adds a 'hint', telling Mongo the proper index to use for the query.

        Judicious use of hints can greatly improve query performance. When
        doing a query on multiple fields (at least one of which is indexed)
        pass the indexed field as a hint to the query. Hinting will not do
        anything if the corresponding index does not exist. Raises
        InvalidOperation if this cursor has already been used.

        `index` should be an index as passed to create_index
        (e.g. [('field', ASCENDING)]). If `index` is None any existing
        hints for this query are cleared. The last hint applied to this cursor
        takes precedence over all others.

        :Parameters:
          - `index`: index to hint on (as an index specifier)
        """
        self.__check_okay_to_chain()
        if index is None:
            self.__hint = None
            return self

        if not isinstance(index, (types.ListType)):
            raise TypeError("hint takes a list specifying an index")
        self.__hint = pymongo._index_document(index)
        return self

    def where(self, code):
        """Adds a $where clause to this query.

        The `code` argument must be an instance of (str, unicode, Code)
        containing a JavaScript expression. This expression will be evaluated
        for each object scanned. Only those objects for which the expression
        evaluates to *true* will be returned as results. The keyword *this*
        refers to the object currently being scanned.

        Raises TypeError if `code` is not an instance of (str, unicode). Raises
        InvalidOperation if this cursor has already been used. Only the last
        where clause applied to a cursor has any effect.

        :Parameters:
          - `code`: JavaScript expression to use as a filter
        """
        self.__check_okay_to_chain()
        if not isinstance(code, Code):
            code = Code(code)

        self.__spec["$where"] = code
        return self

    def _refresh(self):
        """Refreshes the cursor with more data from Mongo.

        Returns the length of self.__data after refresh. Will exit early if
        self.__data is already non-empty. Raises OperationFailure when the
        cursor cannot be refreshed due to an error on the query.
        """
        if len(self.__data) or self.__killed:
            return len(self.__data)

        def send_message(operation, message):
            db = self.__collection.database()
            kwargs = {"_sock": self.__socket,
                      "_must_use_master": self.__must_use_master}
            if self.__connection_id is not None:
                kwargs["_connection_to_use"] = self.__connection_id

            response = db.connection()._receive_message(operation, message,
                                                        **kwargs)

            if isinstance(response, types.TupleType):
                (connection_id, response) = response
            else:
                connection_id = None

            response_flag = struct.unpack("<i", response[:4])[0]
            if response_flag == 1:
                raise OperationFailure("cursor id '%s' not valid at server" %
                                       self.__id)
            elif response_flag == 2:
                error_object = bson.BSON(response[20:]).to_dict()
                if error_object["$err"] == "not master":
                    db.connection()._reset()
                    raise AutoReconnect("master has changed")
                raise OperationFailure("database error: %s" %
                                       error_object["$err"])
            else:
                assert response_flag == 0

            self.__id = struct.unpack("<q", response[4:12])[0]
            self.__connection_id = connection_id
            assert struct.unpack("<i", response[12:16])[0] == self.__retrieved

            number_returned = struct.unpack("<i", response[16:20])[0]
            self.__retrieved += number_returned

            if self.__limit and self.__id and self.__limit <= self.__retrieved:
                self.__die()

            self.__data = bson._to_dicts(response[20:])
            assert len(self.__data) == number_returned

        message = self.__query_options()
        message += bson._make_c_string(self.__collection.full_name())
        if self.__id is None:
            # Query
            message += struct.pack("<i", self.__skip)
            message += struct.pack("<i", self.__limit)
            message += bson.BSON.from_dict(self.__query_spec())
            if self.__fields:
                message += bson.BSON.from_dict(self.__fields)

            send_message(2004, message)
            if not self.__id:
                self.__killed = True
        elif self.__id:
            # Get More
            limit = 0
            if self.__limit:
                if self.__limit > self.__retrieved:
                    limit = self.__limit - self.__retrieved
                else:
                    self.__killed = True
                    return 0

            message += struct.pack("<i", limit)
            message += struct.pack("<q", self.__id)

            send_message(2005, message)

        return len(self.__data)

    def __iter__(self):
        return self

    def next(self):
        db = self.__collection.database()
        if len(self.__data) or self._refresh():
            next = db._fix_outgoing(self.__data.pop(0), self.__collection)
        else:
            raise StopIteration
        return next
