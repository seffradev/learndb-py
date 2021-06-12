from __future__ import annotations
"""
Python prototype/reference implementation
"""
import os.path
import sys

from typing import Union
from dataclasses import dataclass
from enum import Enum, auto
from random import randint  # for testing
import traceback # for testing

from btree import Tree, TreeInsertResult, NodeType

# section: constants

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

PAGE_SIZE = 4096
WORD = 32

TABLE_MAX_PAGES = 100

DB_FILE = 'db.file'

# serialized data layout (row)
ID_SIZE = 6 # length in bytes
BODY_SIZE = 58
ROW_SIZE = ID_SIZE + BODY_SIZE
ID_OFFSET = 0
BODY_OFFSET = ID_OFFSET + ID_SIZE
ROWS_PER_PAGE = PAGE_SIZE // ROW_SIZE


# section: enums

class MetaCommandResult(Enum):
    Success = auto()
    UnrecognizedCommand = auto()


class StatementType(Enum):
    Uninitialized = auto()
    Insert = auto()
    Select = auto()


class PrepareResult(Enum):
    Success = auto()
    UnrecognizedStatement = auto()


class ExecuteResult(Enum):
    Success = auto()
    TableFull = auto()




# section: classes/structs
@dataclass
class Row:
    """
    NOTE: this assumes a fixed table definition. Fixing the
    table definition, like in the tutorial to bootstrap the
    (de)serialize logic.
    Later when I can handle generic schemas this will need to be
    made generic
    """
    identifier : int
    body: str


@dataclass
class Statement:
    statement_type: StatementType
    row_to_insert: Row

# section: helpers


# section : helper objects/functions, e.g. table, pager

def db_open(filename: str) -> Table:
    """
    opens connection to db, i.e. initializes
    table and pager.

    The relationships are: `tree` is a abstracts the pages into a tree
    and maps 1-1 with the logical entity `table`. The table.root_page_num
    is a reference to first

    """
    pager = Pager.pager_open(filename)
    # with one table the root page is hard coded to 0, but
    # with multiple tables I will need a mapping: table_name -> root_page_num
    table = Table(pager, root_page_num=0)
    return table


def db_close(table: Table):
    """
    this calls the pager `close`
    """
    table.pager.close()


class Pager:
    """
    manager of pages in memory (cache)
    and on file
    """
    def __init__(self, filename):
        """
        filename is handled differently from tutorial
        since it passes a fileptr; here I'll manage the file
        with the `Pager` class
        """
        self.pages = [None for _ in range(TABLE_MAX_PAGES)]
        self.filename = filename
        self.fileptr = None
        self.file_length = 0
        self.num_pages = 0
        self.open_file()

    def open_file(self):
        """
        open database file
        """
        # open binary file such that: it is readable, not truncated(random),
        # create if not exists, writable(random)
        # a+b (and more generally any "a") mode can only write to end
        # of file; seeks only applies to read ops
        # r+b allows read and write, without truncation, but errors if
        # the file does not exist
        # NB: this sets the file ptr location to the end of the file
        try:
            self.fileptr = open(self.filename, "r+b")
        except FileNotFoundError:
            self.fileptr = open(self.filename, "w+b")
        self.file_length = os.path.getsize(self.filename)

        if self.file_length % PAGE_SIZE != 0:
            # avoiding exceptions since I want this to be closer to Rust, i.e panic or enum
            print("Db file is not a whole number of pages. Corrupt file.")
            sys.exit(EXIT_FAILURE)

        self.num_pages = self.file_length // PAGE_SIZE

        # warm up page cache, i.e. load data into memory
        # to load data, seek to beginning of file
        self.fileptr.seek(0)
        for page_num in range(self.num_pages):
            self.get_page(page_num)

    @classmethod
    def pager_open(cls, filename):
        """
        this does nothing - keeping it so code is aligned.
        C works with fd (ints), so you can
        open files and pass around an int. For python, I need to
        pass the file ref around.
        """
        return cls(filename)

    def get_unused_page_num(self) -> int:
        """
        NOTE: this depends on num_pages being updated when a new page is requested
        :return:
        """
        return self.num_pages

    def page_exists(self, page_num: int) -> bool:
        """

        :param page_num: does this page exist/ has been allocated
        :return:
        """
        # num_pages counts whole pages
        return page_num < self.num_pages

    def get_page(self, page_num: int) -> bytearray:
        """
        get `page` given `page_num`
        """
        if page_num >= TABLE_MAX_PAGES:
            print(f"Tried to fetch page out of bounds (requested page = {page_num}, max pages = {TABLE_MAX_PAGES})")
            sys.exit(EXIT_FAILURE)

        if self.pages[page_num] is None:
            # cache miss. Allocate memory and load from file.
            page = bytearray(PAGE_SIZE)

            # determine number of pages in file; there should only be complete pages
            num_pages = self.file_length // PAGE_SIZE
            if page_num < num_pages:
                # this page exists on file, load from file
                # into `page`
                self.fileptr.seek(page_num * PAGE_SIZE)
                read_page = self.fileptr.read(PAGE_SIZE)
                assert len(read_page) == PAGE_SIZE, "corrupt file: read page returned byte array smaller than page"
                page[:PAGE_SIZE] = read_page
            else:
                pass

            self.pages[page_num] = page

            if page_num >= self.num_pages:
                self.num_pages += 1

        return self.pages[page_num]

    def close(self):
        """
        close the connection i.e. flush pages to file
        """
        # this is 0-based
        # NOTE: not sure about this +1;
        for page_num in range(self.num_pages):
            if self.pages[page_num] is None:
                continue
            self.flush_page(page_num)

    def flush_page(self, page_num: int):
        """
        flush/write page to file
        page_num is the page to write
        size is the number of bytes to write
        """
        if self.pages[page_num] is None:
            print("Tried to flush null page")
            sys.exit(EXIT_FAILURE)

        byte_offset = page_num * PAGE_SIZE
        self.fileptr.seek(byte_offset)
        to_write = self.pages[page_num]
        self.fileptr.write(to_write)



class Cursor:
    """
    Represents a cursor. A cursor understands
    how to traverse the table and how to insert, and remove
    rows from a table.
    """
    def __init__(self, table: Table, page_num: int = 0):
        self.table = table
        self.tree = table.tree
        self.page_num = page_num
        self.cell_num = 0
        self.end_of_table = False
        self.first_leaf()

    def first_leaf(self):
        """
        set cursor location to left-most/first leaf
        """
        # start with root and descend until we hit left most leaf
        node = self.table.pager.get_page(self.page_num)
        while Tree.get_node_type(node) == NodeType.NodeInternal:
            assert Tree.internal_node_num_keys(node) > 0, "invalid tree with zero keys internal node"
            child_page_num = Tree.internal_node_child(node, 0)
            self.page_num = child_page_num
            node = self.table.pager.get_page(child_page_num)

        self.cell_num = 0
        # node must be leaf node
        self.end_of_table = (Tree.leaf_node_num_cells(node) == 0)

    @classmethod
    def table_start(cls, table: Table) -> Cursor:
        """
        cursor pointing to beginning of table
        """
        return cls(table, 0)

    def get_row(self) -> Row:
        """
        return row pointed by cursor
        :return:
        """
        node = self.table.pager.get_page(self.page_num)
        serialized = Tree.leaf_node_value(node, self.cell_num)
        return Table.deserialize(serialized)

    def insert_row(self, row: Row):
        """
        insert row to location pointed by cursor
        :return:
        """
        serialized = Table.serialize(row)
        self.tree.insert(row.identifier, serialized)

    def advance_old(self):
        """
        advance the cursor
        :return:
        """
        node = self.table.pager.get_page(self.page_num)
        self.cell_num += 1
        # consider caching RHS value
        if self.cell_num >= Tree.leaf_node_num_cells(node):
            self.end_of_table = True

    def next_leaf(self):
        """
        move self.page_num and self.cell_num to next leaf and next cell
        this method requires the self.page_num start at a leaf node.

        NOTE: if starting from an internal node, to get to a leaf use `first_leaf` method
        :return:
        """
        # starting point
        node = self.table.pager.get_page(self.page_num)
        if Tree.is_node_root(node) is True:
            # there is nothing
            self.end_of_table = True
            return

        node_max_value = self.tree.get_node_max_key(node)
        parent_page_num = Tree.get_parent_page_num(node)
        # check if current page, i.e. self.page_num is right most child of it's parent
        parent = self.table.pager.get_page(parent_page_num)
        child_num = self.tree.internal_node_find(parent_page_num, node_max_value)
        if child_num == INTERNAL_NODE_MAX_CELLS:
            # this is the right child; thus all children have been consumed
            # go up another level
            self.page_num = parent_page_num
            self.next_leaf()
        else:
            # there is at least one child to be consumed
            # find the next child
            if child_num == Tree.internal_node_num_keys(parent) - 1:
                # next child is the right child
                next_child = Tree.internal_node_right_child(parent)
            else:
                next_child = Tree.internal_node_child(parent, child_num + 1)
            self.page_num = next_child
            # now find first leaf in next child
            self.first_leaf()

    def advance(self):
        """
        advance the cursor, from left most leaf node to right most leaf node
        :return:
        """
        # advance always start at leaf node and ends at a leaf node;
        # starting at or ending at an internal node means the cursor is inconsistent
        node = self.table.pager.get_page(self.page_num)
        # we are currently on the last cell in the node
        # go to the next node if it exists
        if self.cell_num >= Tree.leaf_node_num_cells(node) - 1:
            self.next_leaf()
        else:
            self.cell_num += 1


class Table:
    """
    Currently `Table` interface is around (de)ser given a row number.
    Ultimately, the table should
    represent the logical-relation-entity, and access to the pager, i.e. the storage
    layer should be done via an Engine, that acts as the storage layer access for
    all tables.
    """
    def __init__(self, pager: Pager, root_page_num: int = 0):
        self.pager = pager
        self.root_page_num = root_page_num
        self.tree = Tree(pager, root_page_num)

    @staticmethod
    def serialize(row: Row) -> bytearray:
        """
        turn row (object) into bytes
        """

        serialized = bytearray(ROW_SIZE)
        ser_id = row.identifier.to_bytes(ID_SIZE, sys.byteorder)
        # strings needs to be encoded
        ser_body = bytes(str(row.body), "utf-8")
        if len(ser_body) > BODY_SIZE:
            raise ValueError("row serialization failed; body too long")

        serialized[ID_OFFSET: ID_OFFSET + ID_SIZE] = ser_id
        serialized[BODY_OFFSET: BODY_OFFSET + len(ser_body)] = ser_body
        return serialized

    @staticmethod
    def deserialize(row_bytes: bytes):
        """

        :param byte_offset:
        :return:
        """
        # read bytes corresponding to columns
        id_bstr = row_bytes[ID_OFFSET: ID_OFFSET + ID_SIZE]
        body_bstr = row_bytes[BODY_OFFSET: BODY_OFFSET + BODY_SIZE]

        # this will need to be revisited when handling other data types
        id_val = int.from_bytes(id_bstr, sys.byteorder)
        # not sure if stripping nulls is valid (for other datatypes)
        body_val = body_bstr.rstrip(b'\x00')
        body_val = body_val.decode('utf-8')
        return Row(id_val, body_val)

# section: core execution/user-interface logic

def is_meta_command(command: str) -> bool:
    return command[0] == '.'


def do_meta_command(command: str, table: Table) -> MetaCommandResult:
    if command == ".quit":
        db_close(table)
        sys.exit(EXIT_SUCCESS)
    elif command == ".btree":
        print("Printing tree" + "-"*50)
        table.tree.print_tree()
        print("Finished printing tree" + "-"*50)
        return MetaCommandResult.Success
    elif command == ".validate":
        print("Validating tree....")
        table.tree.validate()
        print("Validation succeeded.......")
        return MetaCommandResult.Success
    elif command == ".nuke":
        os.remove(DB_FILE)
    return MetaCommandResult.UnrecognizedCommand


def prepare_statement(command: str, statement: Statement) -> PrepareResult:
    """
    prepare a statement
    :param command:
    :param statement: modify in-place to be similar to rust impl

    :return:
    """
    if command.startswith("insert"):
        statement.statement_type = StatementType.Insert
        return PrepareResult.Success
    elif command.startswith("select"):
        statement.statement_type = StatementType.Select
        return PrepareResult.Success
    return PrepareResult.UnrecognizedStatement


def execute_insert(statement: Statement, table: Table) -> ExecuteResult:
    print("executing insert...")
    cursor = Cursor.table_start(table)

    row_to_insert = statement.row_to_insert
    print(f"inserting row with id: [{row_to_insert.identifier}]")
    cursor.insert_row(row_to_insert)
    print(f"insert [{row_to_insert.identifier}] is successful")
    return ExecuteResult.Success


def execute_select(table: Table):
    # get cursor to start of table
    print("executing select...")

    cursor = Cursor.table_start(table)
    while cursor.end_of_table is False:
        print(cursor.get_row())
        cursor.advance()


def execute_statement(statement: Statement, table: Table):
    """
    execute statement
    """
    match statement.statement_type:
        case StatementType.Select:
            execute_select(table)
        case StatementType.Insert:
            execute_insert(statement, table)


def input_handler(input_buffer: str, table: Table):
    """
    handle input buffer; could contain command or meta command
    """
    if is_meta_command(input_buffer):
        match do_meta_command(input_buffer, table):
            case MetaCommandResult.Success:
                return
            case MetaCommandResult.UnrecognizedCommand:
                print("Unrecognized meta command")
                return

    statement = Statement(StatementType.Uninitialized, None)
    match prepare_statement(input_buffer, statement):
        case PrepareResult.Success:
            # will execute below
            pass
        case PrepareResult.UnrecognizedStatement:
            print(f"Unrecognized keyword at start of '{input_buffer}'")
            return

    # handle non-meta command
    execute_statement(statement, table)
    print(f"Executed command '{input_buffer}'")


def next_value(index):


    return randint(1, 1000)

    # vals = [64, 5, 13, 82]
    # vals = [82, 13, 5, 2, 0]
    # vals = [10, 20, 30, 40, 50, 60, 70]
    # vals = [1,2,3,4]
    # vals = [72, 79, 96, 38, 47]
    # vals = [432, 507, 311, 35, 246, 950, 956, 929, 769, 744, 994, 438]
    # vals = [159, 597, 520, 189, 822, 725, 504, 397, 218, 134, 516]
    # vals = [159, 597, 520, 189, 822, 725, 504, 397]
    # vals = [960, 267, 947, 400, 795, 327, 464, 884, 667, 870, 92]
    # vals = [793, 651, 165, 282, 177, 439, 593]
    # vals = [229, 653, 248, 298, 801, 947, 63, 619, 475, 422, 856, 57, 38]
    # vals = [103, 394, 484, 380, 834, 677, 604, 611, 952, 71, 568, 291, 433, 305]
    # vals = [114, 464, 55, 450, 729, 646, 95, 649, 59, 412, 546, 340, 667, 274, 477, 363, 333, 897, 772, 508, 182, 305, 428, 180, 22]
    #vals = [15, 382, 653, 668, 139, 70, 828, 17, 891, 121, 175, 642, 491, 281, 920]
    vals = [967, 163, 791, 938, 939, 196, 104, 465, 886, 355, 58, 251, 928, 758, 535, 737, 357, 125, 171, 58, 838, 572, 745, 999, 417, 393, 458, 292, 904, 158, 286, 900, 859, 668, 183]
    if index >= len(vals):
        return vals[-1]
    return vals[index]



def insert_helper(table, key):
    """
    helper to invoke insert for debugging
    """
    statement = Statement(StatementType.Insert, Row(key, "hello database"))
    execute_statement(statement, table)


def repl():
    """
    repl
    """
    table = db_open(DB_FILE)
    while True:
        input_buffer = input("db > ")
        input_handler(input_buffer, table)


def test():
    os.remove(DB_FILE)
    table = db_open(DB_FILE)

    Tree.print_tree_constants()

    values = []
    for i in range(100):
        value = next_value(i)
        values.append(value)
        try:
            insert_helper(table, value)
            input_handler('.btree', table)
            input_handler('.validate', table)
            print(" ")
        except AssertionError as e:
            print(f"Caught assertion error; values: {values}")
            raise

    # input_handler('select', table)
    # input_handler('.btree', table)
    input_handler('.quit', table)

def many_tests():
    for i in range(100):
        test()


if __name__ == '__main__':
    # repl()
    # test()
    many_tests()
