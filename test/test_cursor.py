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

"""Test the cursor module."""
import unittest
import types
import random
import warnings
import sys
sys.path[0:0] = [""]

from pymongo.errors import InvalidOperation, OperationFailure
from pymongo.cursor import Cursor
from pymongo.database import Database
from pymongo.code import Code
from pymongo import ASCENDING, DESCENDING
from test_connection import get_connection


class TestCursor(unittest.TestCase):

    def setUp(self):
        self.db = Database(get_connection(), "pymongo_test")

    def test_explain(self):
        a = self.db.test.find()
        b = a.explain()
        for _ in a:
            break
        c = a.explain()
        del b["millis"]
        del c["millis"]
        self.assertEqual(b, c)
        self.assert_("cursor" in b)

    def test_hint(self):
        db = self.db
        self.assertRaises(TypeError, db.test.find().hint, 5.5)
        db.test.remove({})
        db.test.drop_indexes()

        for i in range(100):
            db.test.insert({"num": i, "foo": i})

        self.assertRaises(OperationFailure,
                          db.test.find({"num": 17, "foo": 17})
                          .hint([("num", ASCENDING)]).explain)
        self.assertRaises(OperationFailure,
                          db.test.find({"num": 17, "foo": 17})
                          .hint([("foo", ASCENDING)]).explain)

        index = db.test.create_index("num")

        spec = [("num", ASCENDING)]
        self.assertEqual(db.test.find({}).explain()["cursor"], "BasicCursor")
        self.assertEqual(db.test.find({}).hint(spec).explain()["cursor"],
                         "BtreeCursor %s" % index)
        self.assertEqual(db.test.find({}).hint(spec).hint(None)
                         .explain()["cursor"],
                         "BasicCursor")
        self.assertRaises(OperationFailure,
                          db.test.find({"num": 17, "foo": 17})
                          .hint([("foo", ASCENDING)]).explain)

        a = db.test.find({"num": 17})
        a.hint(spec)
        for _ in a:
            break
        self.assertRaises(InvalidOperation, a.hint, spec)

        self.assertRaises(TypeError, db.test.find().hint, index)

    # TODO right now this doesn't actually test anything useful, just that the
    # call doesn't blow up in the normal case.
    def test_slave_okay(self):
        db = self.db
        db.drop_collection("test")

        a = db.test.find(slave_okay=True)
        for _ in a:
            break

        db.test.save({"x": 1})
        self.assertEqual(1, db.test.find(slave_okay=True).next()["x"])
        self.assertEqual(1, db.test.find(slave_okay=False).next()["x"])

    def test_limit(self):
        db = self.db

        self.assertRaises(TypeError, db.test.find().limit, None)
        self.assertRaises(TypeError, db.test.find().limit, "hello")
        self.assertRaises(TypeError, db.test.find().limit, 5.5)

        db.test.remove({})
        for i in range(100):
            db.test.save({"x": i})

        count = 0
        for _ in db.test.find():
            count += 1
        self.assertEqual(count, 100)

        count = 0
        for _ in db.test.find().limit(20):
            count += 1
        self.assertEqual(count, 20)

        count = 0
        for _ in db.test.find().limit(99):
            count += 1
        self.assertEqual(count, 99)

        count = 0
        for _ in db.test.find().limit(1):
            count += 1
        self.assertEqual(count, 1)

        count = 0
        for _ in db.test.find().limit(0):
            count += 1
        self.assertEqual(count, 100)

        count = 0
        for _ in db.test.find().limit(0).limit(50).limit(10):
            count += 1
        self.assertEqual(count, 10)

        a = db.test.find()
        a.limit(10)
        for _ in a:
            break
        self.assertRaises(InvalidOperation, a.limit, 5)

    def test_skip(self):
        db = self.db

        self.assertRaises(TypeError, db.test.find().skip, None)
        self.assertRaises(TypeError, db.test.find().skip, "hello")
        self.assertRaises(TypeError, db.test.find().skip, 5.5)

        db.drop_collection("test")

        for i in range(100):
            db.test.save({"x": i})

        for i in db.test.find():
            self.assertEqual(i["x"], 0)
            break

        for i in db.test.find().skip(20):
            self.assertEqual(i["x"], 20)
            break

        for i in db.test.find().skip(99):
            self.assertEqual(i["x"], 99)
            break

        for i in db.test.find().skip(1):
            self.assertEqual(i["x"], 1)
            break

        for i in db.test.find().skip(0):
            self.assertEqual(i["x"], 0)
            break

        for i in db.test.find().skip(0).skip(50).skip(10):
            self.assertEqual(i["x"], 10)
            break

        for i in db.test.find().skip(1000):
            self.fail()

        a = db.test.find()
        a.skip(10)
        for _ in a:
            break
        self.assertRaises(InvalidOperation, a.skip, 5)

    def test_slice_with_skip(self):
        from itertools import izip, count
        db = self.db
        db.drop_collection("test")

        for i in range(100):
            db.test.save({"x": i})

        for i, v in izip(count(0), db.test.find()):
            self.assertEqual(v["x"], i)

        for i, v in izip(count(20), db.test.find()[20:]):
            self.assertEqual(v["x"], i)

        for i, v in izip(count(99), db.test.find()[99:]):
            self.assertEqual(v["x"], i)

        for i, v in izip(count(1), db.test.find()[1:]):
            self.assertEqual(v["x"], i)

        for i, v in izip(count(0), db.test.find()[0:]):
            self.assertEqual(v["x"], i)

        for i, v in izip(count(60), db.test.find()[0:][50:][10:]):
            self.assertEqual(v["x"], i)

        for i in db.test.find()[1000:]:
            self.fail()

    def test_slice_with_limit(self):
        from itertools import izip, count
        db = self.db
        db.drop_collection("test")

        for i in range(100):
            db.test.save({"x": i})

        for i, v in izip(count(0), db.test.find()):
            self.assertEqual(v["x"], i)


        result = db.test.find()[20:25]
        self.assertEqual(len(result), 5)

        for i, v in izip(count(20), result):
            self.assertEqual(v["x"], i)


        result = db.test.find()[99:100]
        self.assertEqual(len(result), 1)

        for i, v in izip(count(99), result):
            self.assertEqual(v["x"], i)


        result = db.test.find()[1:11]
        self.assertEqual(len(result), 10)

        for i, v in izip(count(1), result):
            self.assertEqual(v["x"], i)


        result = db.test.find()[0:10]
        self.assertEqual(len(result), 10)

        for i, v in izip(count(0), result):
            self.assertEqual(v["x"], i)


        result = db.test.find()[10:50][25:100]
        self.assertEqual(len(result), 15)

        for i, v in izip(count(35), result):
            self.assertEqual(v["x"], i)


        result = db.test.find()[20:50][0:10]
        self.assertEqual(len(result), 10)

        for i, v in izip(count(20), result):
            self.assertEqual(v["x"], i)


        for i in db.test.find()[1000:10]:
            self.fail()

    def test_get_single_item(self):
        db = self.db
        db.drop_collection("test")

        for i in range(100):
            db.test.save({"x": i})

        result = db.test.find()
        self.assertEqual(result[0]['x'], 0)
        self.assertEqual(result[50]['x'], 50)
        self.assertEqual(result[99]['x'], 99)

    def test_length(self):
        from itertools import izip, count
        db = self.db
        db.drop_collection("test")

        for i in range(10):
            db.test.save({"x": i})

        self.assertEqual(len(db.test.find()), 10)

    def test_sort(self):
        db = self.db

        self.assertRaises(TypeError, db.test.find().sort, 5)
        self.assertRaises(ValueError, db.test.find().sort, [])
        self.assertRaises(TypeError, db.test.find().sort, [], ASCENDING)
        self.assertRaises(TypeError, db.test.find().sort,
                          [("hello", DESCENDING)], DESCENDING)
        self.assertRaises(TypeError, db.test.find().sort, "hello", "world")

        db.test.remove({})

        unsort = range(10)
        random.shuffle(unsort)

        for i in unsort:
            db.test.save({"x": i})

        asc = [i["x"] for i in db.test.find().sort("x", ASCENDING)]
        self.assertEqual(asc, range(10))
        asc = [i["x"] for i in db.test.find().sort("x")]
        self.assertEqual(asc, range(10))
        asc = [i["x"] for i in db.test.find().sort([("x", ASCENDING)])]
        self.assertEqual(asc, range(10))

        expect = range(10)
        expect.reverse()
        desc = [i["x"] for i in db.test.find().sort("x", DESCENDING)]
        self.assertEqual(desc, expect)
        desc = [i["x"] for i in db.test.find().sort([("x", DESCENDING)])]
        self.assertEqual(desc, expect)
        desc = [i["x"] for i in
                db.test.find().sort("x", ASCENDING).sort("x", DESCENDING)]
        self.assertEqual(desc, expect)

        expected = [(1, 5), (2, 5), (0, 3), (7, 3), (9, 2), (2, 1), (3, 1)]
        shuffled = list(expected)
        random.shuffle(shuffled)

        db.test.remove({})
        for (a, b) in shuffled:
            db.test.save({"a": a, "b": b})

        result = [(i["a"], i["b"]) for i in
                  db.test.find().sort([("b", DESCENDING),
                                       ("a", ASCENDING)])]
        self.assertEqual(result, expected)

        a = db.test.find()
        a.sort("x", ASCENDING)
        for _ in a:
            break
        self.assertRaises(InvalidOperation, a.sort, "x", ASCENDING)

    def test_count(self):
        db = self.db
        db.test.remove({})

        self.assertEqual(0, db.test.find().count())

        for i in range(10):
            db.test.save({"x": i})

        self.assertEqual(10, db.test.find().count())
        self.assert_(isinstance(db.test.find().count(), types.IntType))
        self.assertEqual(10, db.test.find().limit(5).count())
        self.assertEqual(10, db.test.find().skip(5).count())

        self.assertEqual(1, db.test.find({"x": 1}).count())
        self.assertEqual(5, db.test.find({"x": {"$lt": 5}}).count())

        a = db.test.find()
        b = a.count()
        for _ in a:
            break
        self.assertEqual(b, a.count())

        self.assertEqual(0, db.test.acollectionthatdoesntexist.find().count())

    def test_where(self):
        db = self.db
        db.test.remove({})

        a = db.test.find()
        self.assertRaises(TypeError, a.where, 5)
        self.assertRaises(TypeError, a.where, None)
        self.assertRaises(TypeError, a.where, {})

        for i in range(10):
            db.test.save({"x": i})

        self.assertEqual(3, len(list(db.test.find().where('this.x < 3'))))
        self.assertEqual(3,
                         len(list(db.test.find().where(Code('this.x < 3')))))
        self.assertEqual(3, len(list(db.test.find().where(Code('this.x < i',
                                                               {"i": 3})))))
        self.assertEqual(10, len(list(db.test.find())))

        self.assertEqual(3, db.test.find().where('this.x < 3').count())
        self.assertEqual(10, db.test.find().count())
        self.assertEqual(3, db.test.find().where(u'this.x < 3').count())
        self.assertEqual([0, 1, 2],
                         [a["x"] for a in
                          db.test.find().where('this.x < 3')])
        self.assertEqual([],
                         [a["x"] for a in
                          db.test.find({"x": 5}).where('this.x < 3')])
        self.assertEqual([5],
                         [a["x"] for a in
                          db.test.find({"x": 5}).where('this.x > 3')])

        cursor = db.test.find().where('this.x < 3').where('this.x > 7')
        self.assertEqual([8, 9], [a["x"] for a in cursor])

        a = db.test.find()
        b = a.where('this.x > 3')
        for _ in a:
            break
        self.assertRaises(InvalidOperation, a.where, 'this.x < 3')

    def test_kill_cursors(self):
        db = self.db
        db.drop_collection("test")

        client_cursors = db._command({"cursorInfo": 1})["clientCursors_size"]
        by_location = db._command({"cursorInfo": 1})["byLocation_size"]

        for i in range(10000):
            db.test.insert({"i": i})

        self.assertEqual(client_cursors,
                         db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertEqual(by_location,
                         db._command({"cursorInfo": 1})["byLocation_size"])

        for _ in range(10):
            db.test.find_one()

        self.assertEqual(client_cursors,
                         db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertEqual(by_location,
                         db._command({"cursorInfo": 1})["byLocation_size"])

        for _ in range(10):
            for x in db.test.find():
                break

        self.assertEqual(client_cursors,
                         db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertEqual(by_location,
                         db._command({"cursorInfo": 1})["byLocation_size"])

        a = db.test.find()
        for x in a:
            break

        self.assertNotEqual(
            client_cursors,
            db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertNotEqual(by_location,
                            db._command({"cursorInfo": 1})["byLocation_size"])

        del a

        self.assertEqual(client_cursors,
                         db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertEqual(by_location,
                         db._command({"cursorInfo": 1})["byLocation_size"])

        a = db.test.find().limit(10)
        for x in a:
            break

        self.assertEqual(client_cursors,
                         db._command({"cursorInfo": 1})["clientCursors_size"])
        self.assertEqual(by_location,
                         db._command({"cursorInfo": 1})["byLocation_size"])

    def test_rewind(self):
        self.db.test.save({"x": 1})
        self.db.test.save({"x": 2})
        self.db.test.save({"x": 3})

        cursor = self.db.test.find().limit(2)

        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)

        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(0, count)

        cursor.rewind()
        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)

        cursor.rewind()
        count = 0
        for _ in cursor:
            break
        cursor.rewind()
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)

        self.assertEqual(cursor, cursor.rewind())

    def test_clone(self):
        self.db.test.save({"x": 1})
        self.db.test.save({"x": 2})
        self.db.test.save({"x": 3})

        cursor = self.db.test.find().limit(2)

        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)

        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(0, count)

        cursor = cursor.clone()
        cursor2 = cursor.clone()
        count = 0
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)
        for _ in cursor2:
            count += 1
        self.assertEqual(4, count)

        cursor.rewind()
        count = 0
        for _ in cursor:
            break
        cursor = cursor.clone()
        for _ in cursor:
            count += 1
        self.assertEqual(2, count)

        self.assertNotEqual(cursor, cursor.clone())

    def test_count_with_fields(self):
        self.db.test.remove({})
        self.db.test.save({"x": 1})

        for _ in self.db.test.find({}, ["a"]):
            self.fail()

        self.assertEqual(0, self.db.test.find({}, ["a"]).count())

if __name__ == "__main__":
    unittest.main()
