using System.Collections.Generic;

namespace Core.Models
{
    public class Class
    {
        public int Id { get; set; }
        public string? Name { get; set; }
        // Class belongs to a Grade level
        public int GradeId { get; set; }
        public Grade? Grade { get; set; }

        // Students in this class
        public List<Student> Students { get; set; } = new();
        public int? SchoolId { get; set; }
        public School? School { get; set; }

        // Teaching assignments for this class
        public List<TeachingAssignment> TeachingAssignments { get; set; } = new();
    }
}
