#include "ValueRefParserImpl.h"

#include "EnumParser.h"


namespace {
    struct star_type_parser_rules {
        star_type_parser_rules() {
            qi::_1_type _1;
            qi::_val_type _val;
            using phoenix::new_;
            using phoenix::push_back;

            const parse::lexer& tok = parse::lexer::instance();

            variable_name
                %=   tok.StarType_
                |    tok.NextOlderStarType_
                |    tok.NextYoungerStarType_
                ;

            constant
                =    parse::enum_parser<StarType>() [ _val = new_<ValueRef::Constant<StarType> >(_1) ]
                ;

            initialize_bound_variable_parser<StarType>(bound_variable, variable_name);

            statistic_sub_value_ref
                =   constant
                |   bound_variable
                ;

            initialize_nonnumeric_expression_parsers<StarType>(function_expr, operated_expr, expr, primary_expr);

            initialize_nonnumeric_statistic_parser<StarType>(statistic, statistic_sub_value_ref);

            primary_expr
                =   constant
                |   bound_variable
                |   statistic
                ;

            variable_name.name("StarType variable name (e.g., StarType)");
            constant.name("StarType");
            bound_variable.name("StarType variable");
            statistic.name("StarType statistic");
            primary_expr.name("StarType expression");

#if DEBUG_VALUEREF_PARSERS
            debug(variable_name);
            debug(constant);
            debug(bound_variable);
            debug(statistic);
            debug(primary_expr);
#endif
        }

        typedef parse::value_ref_parser_rule<StarType>::type    rule;
        typedef variable_rule<StarType>::type                   variable_rule;
        typedef statistic_rule<StarType>::type                  statistic_rule;
        typedef expression_rule<StarType>::type                 expression_rule;

        name_token_rule variable_name;
        rule            constant;
        variable_rule   bound_variable;
        rule            statistic_sub_value_ref;
        statistic_rule  statistic;
        expression_rule function_expr;
        expression_rule operated_expr;
        rule            expr;
        rule            primary_expr;
    };
}

namespace parse {
    template <>
    value_ref_parser_rule<StarType>::type& value_ref_parser<StarType>()
    {
        static star_type_parser_rules retval;
        return retval.expr;
    }
}
