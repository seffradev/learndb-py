import abc

from lark import Lark, Transformer, Tree, v_args, ast_utils
from enum import Enum, auto
from typing import Any, List, Union
from dataclasses import dataclass
from .visitor import Visitor


# constants

WHITESPACE = ' '

# enum types


class JoinType(Enum):
    Inner = auto()
    LeftOuter = auto()
    RightOuter = auto()
    FullOuter = auto()
    Cross = auto()


class ColumnModifier(Enum):
    PrimaryKey = auto()
    NotNull = auto()
    Nil = auto()  # no modifier - likely not needed


class DataType(Enum):
    """
    Enums for system datatypes
     NOTE: This represents data-types as understood by the parser. Which
     maybe different from VM's notion of datatypes
    """
    Integer = auto()
    Text = auto()
    Real = auto()
    Blob = auto()


# symbol class

class Symbol(ast_utils.Ast):
    """
    The root of AST hierarchy
    """
    # NOTE: classes with preceding "_" will be skipped

    def accept(self, visitor: Visitor) -> Any:
        return visitor.visit(self)

    def is_virtual(self) -> bool:
        """
        Helper method to determine whether symbol/parsed
        rules' class is virtual, i.e. won't be materialized.
        Classes whose names begins with "_" are virtual.
        :return:
        """
        classname = self.__class__.__name__
        return classname.startswith("_")

    def get_prettychild(self, child, child_depth) -> list:
        """
        Get pretty printed child; calls different method depending on whether
        child is derived from _Symbol, Lark.Tree, or Lark.Token.

        :param child:
        :param child_depth:
        :return:
        """
        if hasattr(child, "prettyprint"):
            # part of Ast hierarchy
            val = child.prettyprint(depth=child_depth)
        elif hasattr(child, "pretty"):
            # part of autogenerated hierarchy
            preceding = WHITESPACE * child_depth
            formatted = f"{preceding}{child.pretty(preceding)}"
            val = [formatted]
        else:
            # token
            preceding = WHITESPACE * child_depth
            formatted = f"{preceding}{str(child)}"
            val = [formatted]
        return val

    def prettyprint(self, depth=0) -> List:
        """
        return a pretty printed string
        :return:
        """
        if hasattr(self, "asdict"):
            children = self.asdict()
        else:
            children = {key: getattr(self, key)
                        for key in dir(self)
                        if (not key.startswith("_") and not callable(getattr(self, key)))}
        lines = []

        child_depth = depth if self.is_virtual() else depth + 1
        preceding = WHITESPACE * depth
        if not self.is_virtual():
            classname = self.__class__.__name__
            lines.append(f'{preceding}{classname}:{os.linesep}')

        for key, value in children.items():
            child = getattr(self, key)
            if isinstance(child, list):
                # list
                for element in child:
                    lines.extend(self.get_prettychild(element, child_depth))
            else:
                # scalar
                lines.extend(self.get_prettychild(child, child_depth))

        return lines

    def prettystr(self) -> str:
        return "".join(self.prettyprint())


# create statement


class CreateStmnt(Symbol):
    def __init__(self, table_name: Tree = None, column_def_list: Tree = None):
        self.table_name = table_name
        self.columns = column_def_list
        self.validate()

    def validate(self):
        """
        Ensure one and only one primary key
        """
        pkey_count = len([col for col in self.columns if col.is_primary_key])
        if pkey_count != 1:
            raise ValueError(f"Expected 1 primary key received {pkey_count}")

    def __repr__(self):
        return str(self)

    def __str__(self):
        return f'{self.__class__.__name__}({self.__dict__})'


# create statement helpers

class ColumnDef(Symbol):

    def __init__(self, column_name: Tree = None, datatype: Tree = None, column_modifier=ColumnModifier.Nil):
        self.column_name = column_name
        self.datatype = datatype
        self.is_primary_key = column_modifier == ColumnModifier.PrimaryKey
        self.is_nullable = column_modifier == ColumnModifier.NotNull or column_modifier == ColumnModifier.PrimaryKey

    def __repr__(self):
        return str(self)

    def __str__(self):
        return self.prettystr()


# select stmnt
@dataclass
class SelectStmnt(Symbol):
    select_clause: Any
    # all other clauses depend on from clause and hence are
    # nested under for_clause; this implicitly means that a select
    # clause can only return a scalar; since if it could return a table
    # we would want to support a condition, and grouping on it; i.e.
    # where, and group by, .. clauses without a from a clause.
    from_clause: Any = None


# select stmnt helpers

@dataclass
class SelectClause(Symbol):
    selectables: List[Any]


class FromClause(Symbol):
    def __init__(self, source, where_clause=None, group_by_clause=None, having_clause=None, order_by_clause=None,
                 limit_clause=None):
        self.source = source
        # where clause can only be defined if a from clause is defined
        self.where_clause = where_clause
        self.group_by_clause = group_by_clause
        self.having_clause = having_clause
        self.order_by_clause = order_by_clause
        self.limit_clause = limit_clause



@dataclass
class SingleSource(Symbol):
    table_name: Any
    table_alias: Any = None


# wrap around from source
@dataclass
class FromSource(Symbol):
    source: Any


class UnconditionedJoin(Symbol):
    def __init__(self, left_source, right_source):
        self.left_source = left_source
        self.right_source = right_source
        self.join_type = JoinType.Cross


class ConditionedJoin(Symbol):
    """
    AST classes are responsible for translating parse tree matched rules
    to more intuitive properties, that the VM can operate on.
    Additionally, they should enforce any local constraints, e.g. 1 primary key
    """

    def __init__(self, left_source, right_source, condition, join_modifier=None):
        self.left_source = left_source
        self.right_source = right_source
        self.condition = condition
        self.join_type = self._join_modifier_to_type(join_modifier)

    @staticmethod
    def _join_modifier_to_type(join_modifier) -> JoinType:
        if join_modifier is None:
            return JoinType.Inner
        modifier = join_modifier.children[0].data  # not sure why it's a list
        modifier = modifier.lower()
        if modifier == "inner":
            return JoinType.Inner
        elif modifier == "left_outer":
            return JoinType.LeftOuter
        elif modifier == "right_outer":
            return JoinType.RightOuter
        else:
            assert modifier == "full_outer"
            return JoinType.FullOuter


class Joining(abc.ABC):
    pass


# makes (Un)ConditionedJoin a subclass of Joining
Joining.register(ConditionedJoin)
Joining.register(UnconditionedJoin)


@dataclass
class WhereClause(Symbol):
    condition: Any  # OrClause


@dataclass
class OrClause(Symbol):
    and_clauses: Any


@dataclass
class AndClause(Symbol):
    predicates: List[Any]


@dataclass
class GroupByClause(Symbol):
    columns: List[Any]


@dataclass
class HavingClause(Symbol):
    condition: Any


@dataclass
class OrderByClause(Symbol):
    columns: List[Any]


@dataclass
class LimitClause(Symbol):
    limit: Any
    offset: Any = None


@dataclass
class InsertStmnt(Symbol):
    table_name: Any
    column_name_list: List[Any]
    value_list: List[Any]



@dataclass
class ColumnNameList(Symbol):
    names: List


@dataclass
class ValueList(Symbol):
    values: List


@dataclass
class DeleteStmnt(Symbol):
    table_name: Any
    where_condition: Any = None


@dataclass
class Program(Symbol):
    statements: list


@dataclass
class TableName(Symbol):
    table_name: Any


@dataclass
class ColumnName(Symbol):
    column_name: Any


# todo: rename ToAst
class ToAst3(Transformer):
    """
    Convert parse tree to AST.
    Handles rules with optionals at tail
    and optionals in body.

    NOTE: another decision point here
    is where I wrap every rule in a dummy symbol class.
    - I could wrap each class, and a parent node can unwrap a child.
    - however, for boolean like fields, e.g. column_def__is_primary_key, it might be better
      to return an enum

    NOTE: If a grammar symbol has a leading "?", the corresponding class won't be visited

    NOTE: methods are organized logically by statement types

    """
    # helpers

    # simple classes - top level statements

    @staticmethod
    def program(args):
        return Program(args)

    @staticmethod
    def create_stmnt(args):
        return CreateStmnt(args[0], args[1])

    @staticmethod
    def select_stmnt(args):
        """select_clause from_clause? group_by_clause? having_clause? order_by_clause? limit_clause?"""
        # this return a logically valid structure,
        # i.e. select is always needed, but where, group by, and having require a from clause
        # and hence are nested under from clause
        return SelectStmnt(*args)

    @staticmethod
    def insert_stmnt(args):
        return InsertStmnt(args*)

    @staticmethod
    def delete_stmnt(args):
        return DeleteStmnt(*args)

    # select stmnt components

    @staticmethod
    def select_clause(args):
        return SelectClause(args)

    @staticmethod
    def from_clause(args) -> FromClause:
        # setup iteration over args
        args_iter = iter(args)
        count = len(args)
        assert count >= 1

        arg = next(args_iter)
        count -= 1
        assert isinstance(arg, FromSource)
        fclause = FromClause(arg)
        if count == 0:
            return fclause

        while count > 0:
            arg = next(args_iter)
            count -= 1
            if isinstance(arg, WhereClause):
                fclause.where_clause = arg
            elif isinstance(arg, GroupByClause):
                fclause.group_by_clause = arg
            elif isinstance(arg, HavingClause):
                fclause.having_clause = arg
            elif isinstance(arg, LimitClause):
                fclause.limit_clause = arg
            elif isinstance(arg, OrderByClause):
                fclause.order_by_clause = arg

        return fclause

    def where_clause(self, args):
        assert len(args) == 1
        return WhereClause(args[0])

    def group_by_clause(self, args):
        return GroupByClause(args)

    def having_clause(self, args):
        return HavingClause(args)

    def order_by_clause(self, args):
        return OrderByClause(args)

    def limit_clause(self, args):
        if len(args) == 1:
            return LimitClause(args[0])
        else:
            assert len(args) == 2
            return LimitClause(*args)

    def source(self, args):
        assert len(args) == 1
        return FromSource(args[0])

    def single_source(self, args):
        assert len(args) <= 2
        name = args[0]
        alias = args[1] if len(args) > 1 else None
        return SingleSource(name, alias)

    def joining(self, args):
        pass

    def conditioned_join(self, args):
        if len(args) == 3:
            return ConditionedJoin(*args)
        else:
            assert len(args) == 4
            return ConditionedJoin(args[0], args[2], args[3], join_modifier=args[1])

    def unconditioned_join(self, args):
        assert len(args) == 2
        return UnconditionedJoin(args[0], args[1])

    def condition(self, args):
        assert len(args) == 1 and isinstance(args[0], OrClause)
        return args[0]

    def or_clause(self, args):
        if len(args) == 1:
            return OrClause([args[0]])
        else:
            assert len(args) == 2
            assert isinstance(args[0], OrClause)
            args[0].and_clauses.append(args[1])
            return args[0]

    def and_clause(self, args):
        if len(args) == 1:
            # requires a list
            return AndClause([args[0]])
        else:
            assert len(args) == 2
            assert isinstance(args[0], AndClause)
            args[0].predicates.append(args[1])
            return args[0]

    def primary(self, args):
        return args[0]


    # create stmnt components

    def table_name(self, args: list):
        assert len(args) == 1
        val = TableName(args[0])
        # breakpoint()
        return val

    def column_def_list(self, args):
        return args

    def column_name(self, args):
        assert len(args) == 1
        val = ColumnName(args[0])
        # breakpoint()
        return val

    def datatype(self, args):
        """
        Convert datatype text to arg
        """
        datatype = args[0].lower()
        if datatype == "integer":
            return DataType.Integer
        elif datatype == "real":
            return DataType.Real
        elif datatype == "text":
            return DataType.Text
        elif datatype == "blob":
            return DataType.Blob
        else:
            raise ValueError(f"Unrecognized datatype [{datatype}]")

    def primary_key(self, arg):
        # this rule doesn't have any children nodes
        #assert len(arg.children) == 0, f"Expected 0 children; received {len(arg.children)}"
        return ColumnModifier.PrimaryKey

    def not_null(self, arg):
        # this rule doesn't have any children nodes
        #assert len(arg.children) == 0
        return ColumnModifier.NotNull
        # breakpoint()

    def column_def(self, args):
        """
        ?column_def       : column_name datatype primary_key? not_null?

        check with if, else conds
        """
        column_name = args[0]
        datatype = args[1]
        # any remaining args are column modifiers
        modifier = ColumnModifier.Nil
        if len(args) >= 3:
            # the logic here is that if the primary key modifier is used
            # not null is redudanct; and the parser ensures/requires primary
            # key mod must be specified before not null
            # todo: this more cleanly, e.g. primary key implies not null, uniqueness
            # modifiers could be a flag enum, which can be or'ed
            modifier = args[2]
        val = ColumnDef(column_name, datatype, modifier)
        return val

    # insert stmnt components

    @staticmethod
    def column_name_list(args):
        return ColumnNameList(args)

    @staticmethod
    def value_list(args):
        return ValueList(args)
